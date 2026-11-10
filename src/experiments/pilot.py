"""Pilot driver (docs/spec.md §8.4). Runs exactly the preregistered pilot:
3 seeds x 8 cells (condition x Claude seat x first mover) = 24 negotiations,
as 8 pilot-mode batches of 3, with the frozen runtime configuration (§8.15).

Everything is checked before the first API call: the budget values, a price
for both approved models, the condition label and the approved runtime of
every cell, and that one call's worst case fits in the budget.

One budget covers the whole pilot, across invocations. It lives in the pilot
database: its caps are fixed when the database is created, every call is
written to a ledger as it is charged, and a later invocation (resume=True)
replays that ledger, so a restart never starts from a fresh cap. A call that
was in flight when an invocation died was never charged, so each run left
'running' or 'failed' is charged one worst-case call. A resume runs only the
(cell, seed) pairs that have no record yet; a pair whose transport reruns were
used up is reported as blocked and never re-run automatically (§8.15). Only
one invocation can use a pilot database at a time.

Pilot data stays out of the confirmatory dataset: it goes to its own
database, every run is stored with mode='pilot', and its seeds (30000-30002)
are disjoint from the full run's (40000-40059).

The full run (§8.3) is the same driver over a bigger design: pass design=FULL
for 60 seeds x 8 cells = 480 negotiations. It runs one instance at a time
through all 8 cells, so drift or a stop can never leave a whole condition
behind, and a pair blocked by transport failures can be re-run cleanly
(retry_blocked=True, §8.11) because the dataset needs all 480.
"""
from __future__ import annotations

import itertools
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.agents.claude_agent import ClaudeAgent
from src.agents.generation import GenerationConfig
from src.agents.openai_agent import OpenAIAgent
from src.agents.prompting import INSTRUCTION_VARIANTS
from src.experiments import provenance
from src.experiments.config import (APPROVED_MAX_ROUNDS, APPROVED_MAX_TRANSPORT_RERUNS,
                                    APPROVED_PRICES, ExperimentConfig, check_approved_runtime)
from src.experiments.runner import check_condition_label, make_budget, run_batch
from src.negotiation.usage import Budget, Prices
from src.storage.db import (append_ledger, get_connection, ledger_calls, load_budget_caps,
                            save_budget_caps)
from src.zero_cost import require_paid_calls

PILOT_SEEDS = (30000, 30001, 30002)
CONDITIONS = ("baseline_v1", "structured_v1")
assert set(CONDITIONS) == set(INSTRUCTION_VARIANTS)
# A batch's instance seeds are base_seed + index, so the pilot seeds must be
# consecutive for one batch per cell to cover exactly them.
assert PILOT_SEEDS == tuple(range(PILOT_SEEDS[0], PILOT_SEEDS[0] + len(PILOT_SEEDS)))


@dataclass(frozen=True)
class PilotCell:
    index: int         # 1..8, in the preregistered order
    condition: str     # baseline_v1 | structured_v1
    claude_seat: str   # "A" or "B"; GPT takes the other seat
    first_mover: str   # "A" or "B" (fixed first-mover policy of the batch)


PILOT_CELLS = tuple(
    PilotCell(i, condition, claude_seat, first_mover)
    for i, (condition, claude_seat, first_mover)
    in enumerate(itertools.product(CONDITIONS, "AB", "AB"), start=1)
)


@dataclass(frozen=True)
class Design:
    mode: str    # the runs' mode: "pilot" or "full"
    label: str   # how messages name it
    seeds: tuple


PILOT = Design("pilot", "pilot", PILOT_SEEDS)
FULL = Design("full", "full run", tuple(range(40000, 40060)))


def pilot_matrix(design: Design = PILOT) -> List[tuple]:
    """The (cell, seed) pairs the design runs: 24 for the pilot, 480 for the full run."""
    return [(cell, seed) for cell in PILOT_CELLS for seed in design.seeds]


def pilot_config(cell: PilotCell, max_total_tokens=None, max_cost_usd=None,
                 max_input_tokens_per_call=None, base_seed: Optional[int] = None,
                 num_negotiations: Optional[int] = None, design: Design = PILOT) -> ExperimentConfig:
    return ExperimentConfig(
        mode=design.mode,
        method=cell.condition,
        model_A_label="claude" if cell.claude_seat == "A" else "openai",
        model_B_label="openai" if cell.claude_seat == "A" else "claude",
        num_negotiations=len(design.seeds) if num_negotiations is None else num_negotiations,
        base_seed=design.seeds[0] if base_seed is None else base_seed,
        max_rounds=APPROVED_MAX_ROUNDS,
        num_categories=3,
        first_mover_policy=cell.first_mover,
        max_transport_reruns=APPROVED_MAX_TRANSPORT_RERUNS,
        budget_max_total_tokens=max_total_tokens,
        budget_max_cost_usd=max_cost_usd,
        budget_max_input_tokens_per_call=max_input_tokens_per_call,
    )


def pilot_agents(cell: PilotCell, claude_cfg: GenerationConfig, openai_cfg: GenerationConfig,
                 client_factory: Optional[Callable[[str], object]] = None) -> tuple:
    """(agent_A, agent_B) for a cell. client_factory(kind) injects offline
    stub clients in tests; None = the real SDK clients."""
    client = client_factory or (lambda kind: None)
    claude = ClaudeAgent(claude_cfg, client=client("claude"), instructions_variant=cell.condition)
    gpt = OpenAIAgent(openai_cfg, client=client("openai"), instructions_variant=cell.condition)
    return (claude, gpt) if cell.claude_seat == "A" else (gpt, claude)


def pilot_worst_case(budget: Budget, negotiations: int = len(PILOT_CELLS) * len(PILOT_SEEDS)) -> dict:
    """Upper bound for `negotiations` planned negotiations if every one uses
    all rounds and every call its reserved worst case (transport reruns, which
    re-run whole negotiations, can add up to x(1 + max_transport_reruns))."""
    calls_per_model = negotiations * math.ceil(APPROVED_MAX_ROUNDS / 2)
    tokens, cost = 0, 0.0
    for model, (max_in, max_out, attempts) in budget.reserve.items():
        tokens += calls_per_model * attempts * (max_in + max_out)
        price = (budget.prices or {}).get(model)
        cost += calls_per_model * attempts * (max_in * price[0] + max_out * price[1]) / 1e6 if price else math.nan
    return {"calls_per_model": calls_per_model, "worst_case_tokens": tokens, "worst_case_cost_usd": cost}


# ------------------------------------------------------- persistent budget
class PersistentBudget(Budget):
    """The pilot's Budget, kept in its database: each charge is written to the
    ledger before the next call can start."""

    def __init__(self, conn: sqlite3.Connection, budget: Budget):
        super().__init__(budget.max_total_tokens, budget.max_cost_usd, budget.prices, budget.reserve)
        self._conn = conn

    def add(self, call: dict) -> None:
        tokens, cost = self.spent_tokens, self.spent_cost_usd
        super().add(call)
        append_ledger(self._conn, call, self.spent_tokens - tokens, self.spent_cost_usd - cost)


def _restore(budget: Budget, conn: sqlite3.Connection) -> Budget:
    """Replay the ledger into `budget` (without writing it again) and charge
    one worst-case call per interrupted run. Every replayed call must charge
    what it charged when recorded, or the ledger is refused."""
    for n, (call, charged_tokens, charged_cost) in enumerate(ledger_calls(conn), start=1):
        tokens, cost = budget.spent_tokens, budget.spent_cost_usd
        Budget.add(budget, call)
        if (budget.spent_tokens - tokens != charged_tokens
                or not math.isclose(budget.spent_cost_usd - cost, charged_cost, rel_tol=1e-9, abs_tol=1e-12)):
            raise ValueError(f"Budget ledger row {n} does not replay to the charge recorded for it; "
                             "refusing to trust this pilot's spend.")
    interrupted = conn.execute("SELECT COUNT(*) FROM runs WHERE status IN ('running', 'failed')").fetchone()[0]
    tokens, cost = budget.next_call_reserve()
    budget.spent_tokens += interrupted * tokens
    budget.spent_cost_usd += interrupted * cost
    return budget


def _read_only(db_path) -> sqlite3.Connection:
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)


def _is_design_db(path: Path, design: Design) -> bool:
    """A database of this design: budget caps set, and no runs of another mode
    (one with caps and no run yet could be either, so it counts)."""
    try:
        conn = _read_only(path)
        try:
            modes = {m for m, in conn.execute("SELECT DISTINCT mode FROM runs")}
            return load_budget_caps(conn) is not None and (not modes or design.mode in modes)
        finally:
            conn.close()
    except sqlite3.Error:
        return False


@contextmanager
def _single_invocation(db_path):
    """An exclusive lock on <db>.lock for the whole invocation, so two
    invocations can never run (and spend) at once. The OS releases it if the
    process dies."""
    lock = sqlite3.connect(f"{db_path}.lock", timeout=0, isolation_level=None)
    try:
        lock.execute("BEGIN EXCLUSIVE")
    except sqlite3.OperationalError as exc:
        lock.close()
        raise ValueError(f"Another pilot invocation is using {db_path}; refusing to start a second one.") from exc
    try:
        yield
    finally:
        lock.close()


# ---------------------------------------------------------------- progress
def pilot_progress(conn: sqlite3.Connection, claude_cfg: GenerationConfig,
                   openai_cfg: GenerationConfig, prices: Optional[Prices],
                   design: Design = PILOT) -> dict:
    """{(cell index, seed): 'done' | 'blocked'} for the pairs that need no
    further run; pairs absent from it are still to run. Raises ValueError if
    the database holds anything but this pilot under the current protocol,
    prompts, spec and runtime, or a (cell, seed) recorded more than once."""
    cells = {(c.condition, c.claude_seat, c.first_mover): c.index for c in PILOT_CELLS}
    expect = {"protocol_id": provenance.PROTOCOL_ID, "spec_version": provenance.spec_version(),
              "prompt_hash": provenance.prompt_hash()}
    generation = {"ClaudeAgent": claude_cfg.to_dict(), "OpenAIAgent": openai_cfg.to_dict()}
    stored_prices = json.loads(json.dumps(prices))
    runs = {}
    for run_id, mode, method, policy, base_seed, cfg_json, *prov in conn.execute(
            "SELECT run_id, mode, method, first_mover_policy, base_seed, config_json, "
            "protocol_id, spec_version, prompt_hash FROM runs"):
        cfg = json.loads(cfg_json)
        seat = "A" if cfg["agents"]["A"]["class"] == "ClaudeAgent" else "B"
        problems = [f"{k}={v!r}" for k, v in zip(expect, prov) if v != expect[k]]
        problems += [f"{a['class']} settings" for a in cfg["agents"].values()
                     if a["generation_config"] != generation.get(a["class"])]
        if cfg["prices_usd_per_million_tokens"] != stored_prices:
            problems.append("prices")
        if mode != design.mode or (method, seat, policy) not in cells or problems:
            raise ValueError(f"Run {run_id} is not this {design.label} under the current configuration "
                             f"(mode={mode}, cell={(method, seat, policy)}, differs: {problems or 'cell'}).")
        runs[run_id] = (cells[(method, seat, policy)], base_seed)

    def pair(run_id, index):
        cell, base_seed = runs[run_id]
        return cell, base_seed + index

    progress, done = {}, []
    for run_id, index, seed in conn.execute("SELECT run_id, negotiation_index, instance_seed FROM negotiations"):
        key = pair(run_id, index)
        if key[1] != seed or seed not in design.seeds:
            raise ValueError(f"Run {run_id} negotiation {index} has seed {seed}, expected {key[1]}.")
        done.append(key)
    dupes = sorted(k for k in set(done) if done.count(k) > 1)
    if dupes:
        raise ValueError(f"(cell, seed) recorded more than once: {dupes}")
    for run_id, index in conn.execute(
            "SELECT run_id, negotiation_index FROM aborted_negotiations WHERE reason = 'transport_failure'"):
        progress[pair(run_id, index)] = "blocked"  # overwritten below if it was later completed
    progress.update({k: "done" for k in done})
    return progress


def _remaining(progress: dict, design: Design = PILOT, retry_blocked: bool = False) -> List[tuple]:
    """(cell, base_seed, n) batches covering the pairs still to run. Pilot: per
    cell, one batch per consecutive run of missing seeds (all 8 cells x 3 seeds
    on a fresh database, exactly the preregistered batches). Full run: one
    single-negotiation batch per pair, instance by instance."""
    def pending(cell, seed):
        state = progress.get((cell.index, seed))
        return state is None or (retry_blocked and state == "blocked")

    if design.mode == "full":
        return [(cell, seed, 1) for seed in design.seeds for cell in PILOT_CELLS if pending(cell, seed)]
    batches = []
    for cell in PILOT_CELLS:
        todo = [s for s in design.seeds if pending(cell, s)]
        for _, group in itertools.groupby(enumerate(todo), lambda p: p[1] - p[0]):
            seeds = [s for _, s in group]
            batches.append((cell, seeds[0], len(seeds)))
    return batches


# ------------------------------------------------------------------ driver
def prepare_pilot(claude_cfg: GenerationConfig, openai_cfg: GenerationConfig, db_path,
                  max_total_tokens=None, max_cost_usd=None, max_input_tokens_per_call=None,
                  prices: Optional[Prices] = APPROVED_PRICES, client_factory=None, resume: bool = False,
                  design: Design = PILOT, retry_blocked: bool = False):
    """Validate everything and build (plan, budget): the batches still to run
    and the budget with everything already charged. Raises ValueError before
    any API call if anything is missing, invalid or unapproved. Writes nothing:
    a resumed database is only read."""
    db_path = Path(db_path)
    if resume and not _is_design_db(db_path, design):
        raise ValueError(f"{db_path} is not an existing {design.label} database; there is no {design.label} to resume.")
    if not resume:
        if db_path.exists():
            raise ValueError(f"{db_path} already exists; the {design.label} must go to a new database "
                             "(or resume it explicitly).")
        others = sorted(p.name for p in db_path.parent.glob("*.db") if _is_design_db(p, design))
        if others:
            raise ValueError(f"{design.label.capitalize()} database(s) {others} already exist in {db_path.parent}; a new "
                             f"{design.label} would start a fresh budget. Resume that {design.label} instead.")
    if max_total_tokens is None and max_cost_usd is None:
        raise ValueError(f"The {design.label} requires a token cap and/or a dollar cap.")
    if max_input_tokens_per_call is None:  # without it no call's worst case is reserved
        raise ValueError(f"The {design.label} requires max_input_tokens_per_call.")
    missing = sorted(m for m in (claude_cfg.model, openai_cfg.model) if not prices or m not in prices)
    if missing:
        raise ValueError(f"No price entry for models: {missing}")
    agents = {}
    for cell in PILOT_CELLS:
        config = pilot_config(cell, max_total_tokens, max_cost_usd, max_input_tokens_per_call, design=design)
        agents[cell] = pilot_agents(cell, claude_cfg, openai_cfg, client_factory)
        check_condition_label(config, list(agents[cell]))
        check_approved_runtime(config, list(agents[cell]))
    budget = make_budget(pilot_config(PILOT_CELLS[0], max_total_tokens, max_cost_usd, max_input_tokens_per_call,
                                      design=design),
                         list(agents[PILOT_CELLS[0]]), prices)
    progress = {}
    if resume:
        conn = _read_only(db_path)
        try:
            caps = dict(max_total_tokens=max_total_tokens, max_cost_usd=max_cost_usd,
                        max_input_tokens_per_call=max_input_tokens_per_call)
            stored = load_budget_caps(conn)
            if stored != caps:
                raise ValueError(f"{db_path} was opened with budget caps {stored}; a resume must use "
                                 f"the same caps, not {caps}.")
            progress = pilot_progress(conn, claude_cfg, openai_cfg, prices, design)
            _restore(budget, conn)
        finally:
            conn.close()
    plan = [(cell, pilot_config(cell, max_total_tokens, max_cost_usd, max_input_tokens_per_call, base, n, design),
             *agents[cell]) for cell, base, n in _remaining(progress, design, retry_blocked)]
    why = budget.exceeded()
    if plan and why:
        raise ValueError(f"Budget cannot cover even the first call of this invocation: {why}")
    return plan, budget


async def run_pilot(claude_cfg: GenerationConfig, openai_cfg: GenerationConfig, db_path,
                    max_total_tokens=None, max_cost_usd=None, max_input_tokens_per_call=None,
                    prices: Optional[Prices] = APPROVED_PRICES, client_factory=None,
                    resume: bool = False, design: Design = PILOT, retry_blocked: bool = False,
                    confirmed_full_run: bool = False) -> dict:
    """Run the pilot's remaining batches in order with its one persistent
    budget. Stops at the first batch that does not complete all its
    negotiations (budget stop or exhausted transport reruns); that batch's run
    row records why. resume=True continues an existing pilot database."""
    if client_factory is None:  # real SDK clients: refused before any file or lock is created
        require_paid_calls(f"the paid {design.label}")
    caps = dict(max_total_tokens=max_total_tokens, max_cost_usd=max_cost_usd,
                max_input_tokens_per_call=max_input_tokens_per_call)
    options = dict(prices=prices, client_factory=client_factory, resume=resume, design=design, retry_blocked=retry_blocked)
    prepare_pilot(claude_cfg, openai_cfg, db_path, **caps, **options)  # refuse before any lock file
    with _single_invocation(db_path):
        plan, prepared = prepare_pilot(  # again under the lock: another invocation may have run meanwhile
            claude_cfg, openai_cfg, db_path, **caps, **options)
        conn = get_connection(db_path)
        try:
            if not resume:
                save_budget_caps(conn, caps)
            budget = _restore(PersistentBudget(conn, prepared), conn)  # same caps and reserve, spend from the DB
            batches, status = [], "completed"
            for cell, config, agent_A, agent_B in plan:
                records = await run_batch(config, agent_A, agent_B, db_path, prices=prices, budget=budget,
                                          confirmed_full_run=confirmed_full_run)
                batches.append({"cell": cell, "base_seed": config.base_seed, "negotiations": len(records)})
                if len(records) != config.num_negotiations:
                    status = "stopped"
                    break
            progress = pilot_progress(conn, claude_cfg, openai_cfg, prices, design)
        finally:
            conn.close()
    blocked = sorted(k for k, v in progress.items() if v == "blocked")
    done = sum(v == "done" for v in progress.values())
    if status == "completed" and done != len(pilot_matrix(design)):
        status = "incomplete"
    return {"status": status, "batches": batches, "completed_negotiations": done, "blocked": blocked,
            "spent_tokens": budget.spent_tokens, "spent_cost_usd": budget.spent_cost_usd}
