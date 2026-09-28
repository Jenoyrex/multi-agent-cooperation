"""Repeated, role-balanced evaluation of two agents (Agent Lab).

Design "balanced-4-distinct/v2" (docs/phase2-agent-lab-plan.md §7). An
evaluation is B blocks; each block is 4 negotiations, one per cell, and every
negotiation gets its OWN hidden scenario:

    c1  X in seat A, A moves first      c3  X in seat B, A moves first
    c2  X in seat A, B moves first      c4  X in seat B, B moves first

No scenario is used twice, so an agent that remembers earlier negotiations
never holds a valuation that belongs to a later opponent. Scenario seeds are
HMAC(LAB_SCENARIO_SECRET, set_id, block, cell): reproducible for whoever holds
the secret, unrecoverable for an agent. Seeds exist only as local variables
while a scenario is built; they are never stored, returned or logged.

Stored as JSON under sandbox_runs/lab/ (never results/). While any evaluation
runs, the lab API is sealed: progress only, for every evaluation.
Aggregates use complete blocks only. Descriptive only: no significance tests.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import re
import statistics
import threading
import time
import traceback
import uuid
from typing import Optional

from dashboard import data as D
from dashboard import sandbox
from lab import adapters, metrics
from src.agents.prompting import INSTRUCTION_VARIANTS
from src.environment.optimum import compute_optimal_welfare
from src.environment.resources import DEFAULT_CATEGORY_NAMES, generate_resource_pool
from src.environment.valuations import generate_valuation
from src.experiments import provenance as P
from src.experiments.config import APPROVED_MAX_ROUNDS, APPROVED_PRICES
from src.negotiation.protocol import EvaluatorState, NegotiationAborted, NegotiationSession
from src.negotiation.usage import Budget
from src.storage.db import _turn_to_dict

DESIGN = "balanced-4-distinct/v2"
LAB_DIR = sandbox.SANDBOX_DIR / "lab"
SECRET_ENV, MIN_SECRET_BYTES = "LAB_SCENARIO_SECRET", 32
SECRET_MISSING = (f"Agent Lab needs a persistent scenario secret. Add {SECRET_ENV} to .env (at least 64 hex "
                  "characters, for example the output of: python -c \"import secrets; print(secrets.token_hex(32))\") "
                  "and restart the server. It keeps hidden scenarios secret from the agents and reproducible for you; "
                  "it is never shown, stored with results or sent to an agent. No evaluation was started.")
MAX_BLOCKS = 10
CATEGORY_NAMES = DEFAULT_CATEGORY_NAMES + ["gizmos", "parts"]  # K <= 3 keeps the Phase-1 names
MIN_CATS, MAX_CATS = 2, len(CATEGORY_NAMES)
MAX_COST_USD = 2.0   # per evaluation, provider calls only
MAX_ABORTS = 3       # stop instead of repeating a failing call
MIN_SPREAD_N = 5     # below this, quartiles are "insufficient observations"
SCRIPTED_PACE_S = 0.15
CELLS = {  # cell -> (seats, first mover)
    "c1": ({"A": "X", "B": "Y"}, "A"), "c2": ({"A": "X", "B": "Y"}, "B"),
    "c3": ({"A": "Y", "B": "X"}, "A"), "c4": ({"A": "Y", "B": "X"}, "B"),
}
# What a sealed view may show about a trial: nothing an agent does not already see.
PUBLIC_TRIAL_FIELDS = ("position", "block", "status", "outcome", "rounds", "seats", "first_mover")


class _ProviderBudget(Budget):
    """The cost cap counts the server's own provider calls. An HTTP endpoint
    reports no usage (its owner pays for it), so its calls are not charged."""

    def add(self, call: dict) -> None:
        if not str(call.get("model") or "").startswith("http:"):
            super().add(call)


# ---------------------------------------------------------------- secret and scenarios

def _secret() -> bytes:
    """The scenario key from the environment; refuses to run without one."""
    try:
        key = bytes.fromhex(os.environ.get(SECRET_ENV, "").strip())
    except ValueError:
        key = b""
    if len(key) < MIN_SECRET_BYTES:
        raise adapters.SpecError(SECRET_MISSING)
    return key


def ready() -> Optional[str]:
    """None if evaluations can start, else the configuration error to show."""
    try:
        _secret()
    except adapters.SpecError as exc:
        return str(exc)
    return None


def key_id(key: bytes) -> str:
    """A one-way label for the key, so reports can say which key built them."""
    return hashlib.sha256(b"key-id|" + key).hexdigest()[:12]


def _derive(key: bytes, set_id: str, *parts) -> int:
    msg = "|".join(["agent-lab/v2", set_id, *map(str, parts)]).encode()
    return int.from_bytes(hmac.new(key, msg, hashlib.sha256).digest()[:8], "big")


def fingerprint(pool: dict, val_a: dict, val_b: dict) -> str:
    return hashlib.sha256(json.dumps([pool, val_a, val_b], sort_keys=True).encode()).hexdigest()


def scenario(key: bytes, set_id: str, block: int, cell: str, k: int) -> dict:
    """One hidden scenario, from the unchanged Phase-1 generators. The derived
    seed stays in this function: nothing it returns contains it."""
    seed = _derive(key, set_id, "scenario", block, cell)
    pool = generate_resource_pool(seed=seed, num_categories=k, category_names=CATEGORY_NAMES[:k])
    val = {r: generate_valuation(seed, r, pool) for r in "AB"}
    opt_alloc, opt = compute_optimal_welfare(pool, val["A"], val["B"])
    return {"pool": pool, "valuation": val, "optimal_allocation": opt_alloc, "optimal_welfare": opt,
            "fingerprint": fingerprint(pool, val["A"], val["B"])}


def plan(key: bytes, set_id: str, blocks: int) -> list[dict]:
    """Every negotiation in run order: each block's 4 cells in a keyed random order."""
    out = []
    for b in range(blocks):
        order = list(CELLS)
        random.Random(_derive(key, set_id, "order", b)).shuffle(order)
        for cell in order:
            seats, first = CELLS[cell]
            out.append({"position": len(out), "block": b, "cell": cell, "seats": seats, "first_mover": first})
    return out


# ---------------------------------------------------------------- request

def validate(req) -> tuple[dict, dict]:
    """Normalize a request. Returns (config, session tokens by agent id)."""
    if not isinstance(req, dict):
        raise adapters.SpecError("The request must be a JSON object.")
    agents = req.get("agents") or {}
    specs, tokens = {}, {}
    for aid in "XY":
        specs[aid], tokens[aid] = adapters.validate_agent(agents.get(aid), f"Agent {aid}")
    sc = req.get("scenario") or {}
    n, k = sc.get("blocks"), sc.get("categories", 3)
    for v, lo, hi, what in ((n, 1, MAX_BLOCKS, "The number of blocks"), (k, MIN_CATS, MAX_CATS, "The number of resource types")):
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise adapters.SpecError(f"{what} must be a whole number from {lo} to {hi}.")
    strategy = req.get("strategy")
    if strategy not in INSTRUCTION_VARIANTS:
        raise adapters.SpecError("Choose the baseline or the structured instructions.")
    if missing := adapters.missing_keys(specs.values()):
        raise adapters.SpecError(f"Missing API key: add {', '.join(missing)} to .env and restart the server. "
                                 "No evaluation was started.")
    return {"agents": specs, "scenario": {"blocks": n, "categories": k}, "strategy": strategy, "design": DESIGN}, tokens


# ---------------------------------------------------------------- one evaluation

class Evaluation:
    def __init__(self, cfg: dict, key: bytes):
        self.id, self.set_id = uuid.uuid4().hex[:12], uuid.uuid4().hex
        self.cfg, self.created, self.key_id = cfg, time.time(), key_id(key)
        self.planned = plan(key, self.set_id, cfg["scenario"]["blocks"])
        self.status, self.trials, self.error, self.current, self.provenance = "running", [], None, None, None
        self._lock = threading.Lock()

    def view(self) -> dict:
        """The full report. Served only while no evaluation runs (see get/listing)."""
        with self._lock:
            trials = list(self.trials)
            return {"kind": "agent-lab", "exploratory": True, "id": self.id, "created": self.created,
                    "status": self.status, "config": self.cfg,
                    "agents": {aid: adapters.describe(s) for aid, s in self.cfg["agents"].items()},
                    "max_rounds": APPROVED_MAX_ROUNDS, "planned": len(self.planned), "current": self.current,
                    "trials": trials, "summary": summarize(trials, self.cfg["scenario"]["blocks"]), "error": self.error,
                    "provenance": self.provenance}


def _provenance(ev: Evaluation) -> dict:
    """What is needed to reproduce this evaluation and to match it against another."""
    cfg, k = ev.cfg, ev.cfg["scenario"]["categories"]
    return {"design": DESIGN, "protocol_id": P.PROTOCOL_ID, "code_version": P.code_version(),
            "prompt_hash": P.prompt_hash(), "dependency_versions": P.dependency_versions(),
            "environment": {**P.environment_params(k), "category_names": CATEGORY_NAMES[:k],
                            "seed_derivation": f"HMAC-SHA256({SECRET_ENV}, agent-lab/v2|set_id|scenario|block|cell)[:8] "
                                               "-> Phase-1 generate_resource_pool / generate_valuation"},
            "contract": adapters.CONTRACT, "key_id": ev.key_id, "set_id": ev.set_id,
            "max_rounds": APPROVED_MAX_ROUNDS, "strategy": cfg["strategy"],
            "outcome_metrics": "src/evaluation/metrics.py", "behavior_metrics": "lab/metrics.py",
            "agents": {aid: {k2: v for k2, v in s.items() if k2 != "auth"} for aid, s in cfg["agents"].items()}}


def _trial_result(record, t: dict) -> dict:
    row = D.summarize(record, f"{t['position']}", "agent-lab")
    by_seat = {seat: metrics.behavior(record, seat) for seat in "AB"}
    ender = metrics.ended_by(record)
    per_agent = {}
    for seat, aid in t["seats"].items():
        per_agent[aid] = {"seat": seat, "moved_first": record.first_mover == seat,
                          "utility": (record.utility_A if seat == "A" else record.utility_B) / 100,
                          "walked_away": record.outcome == "walked_away" and ender == seat,
                          "invalid": record.outcome == "invalid_action" and ender == seat,
                          **by_seat[seat]}
    events = D.events_for([_turn_to_dict(x) for x in record.transcript], record.calls, record)
    models = {seat: sorted({c["response_model"] for c in record.calls if c.get("actor") == seat and c.get("response_model")})
              for seat in "AB"}
    return {**{k: row[k] for k in ("outcome", "invalid_reason", "rounds", "max_rounds", "utility_A", "utility_B",
                                   "rwe", "ew", "sw", "swe", "equitability", "envy_free")},
            "invalid_detail": record.invalid_detail, "final_allocation": record.final_allocation,
            "per_agent": per_agent, "events": events, "models_observed": models, "cost_usd": record.cost_usd}


def execute(ev: Evaluation, key: bytes, tokens: dict) -> None:
    """Run every planned negotiation (blocking). Never raises."""
    cfg, aborts = ev.cfg, 0
    try:
        agents = {aid: adapters.build_agent(cfg["agents"][aid], tokens[aid], cfg["strategy"]) for aid in "XY"}
        tokens.clear()  # the adapters hold the only copies now
        prov = _provenance(ev)
        with ev._lock:
            ev.provenance = prov
        paid = any(s["kind"] in sandbox.MODELS for s in cfg["agents"].values())
        budget = _ProviderBudget(max_cost_usd=MAX_COST_USD, prices=APPROVED_PRICES) if paid else None
        pace = SCRIPTED_PACE_S if all(s["kind"] == "scripted" for s in cfg["agents"].values()) else 0
        for t in ev.planned:
            with ev._lock:
                ev.current = t["position"]
            sc = scenario(key, ev.set_id, t["block"], t["cell"], cfg["scenario"]["categories"])
            a, b = agents[t["seats"]["A"]], agents[t["seats"]["B"]]
            state = EvaluatorState(
                instance_seed=0,  # the derived seed never enters a record
                resource_pool=sc["pool"], valuation_A=sc["valuation"]["A"], valuation_B=sc["valuation"]["B"],
                optimal_allocation=sc["optimal_allocation"], optimal_welfare=sc["optimal_welfare"],
                max_rounds=APPROVED_MAX_ROUNDS, first_mover=t["first_mover"], first_mover_policy="balanced",
                method=cfg["strategy"], model_A_name=a.name, model_B_name=b.name,
                run_id=ev.id, negotiation_index=t["position"])
            if pace:
                time.sleep(pace)
            trial = {**t, "status": "finished", "started_at": time.time(), "fingerprint": sc["fingerprint"],
                     "resource_pool": sc["pool"], "valuation_A": sc["valuation"]["A"], "valuation_B": sc["valuation"]["B"],
                     "optimal_welfare": sc["optimal_welfare"]}
            try:
                record = asyncio.run(NegotiationSession(a, b, state, budget=budget, prices=APPROVED_PRICES).run())
                trial.update(_trial_result(record, t))
            except NegotiationAborted as exc:
                aborts += 1
                aid = t["seats"][exc.actor]
                kind = cfg["agents"][aid]["kind"]
                cls = ("budget_exhausted" if exc.reason == "budget_exhausted"
                       else "agent_failure" if kind == "http" else "provider_failure")
                trial.update(status="aborted", abort={
                    "class": cls, "reason": exc.reason, "round": exc.round_number, "agent": aid, "seat": exc.actor,
                    "attributed": cls == "agent_failure", **_explain(cls, exc, aid, adapters.describe(cfg["agents"][aid])["label"])})
            trial["ended_at"] = time.time()
            with ev._lock:
                ev.trials.append(trial)
            if trial["status"] == "aborted" and (trial["abort"]["class"] == "budget_exhausted" or aborts >= MAX_ABORTS):
                raise _Stop(trial["abort"]["title"])
        _finish(ev, "finished")
    except _Stop as why:
        _finish(ev, "stopped", {"title": str(why), "message": "The evaluation stopped early. Blocks that did not "
                                "finish all 4 negotiations are excluded from every aggregate."})
    except Exception as exc:  # noqa: BLE001 — shown as a safe message; the traceback goes to the server log
        traceback.print_exc()
        _finish(ev, "stopped", {"title": "Unexpected error", "detail": type(exc).__name__,
                                "message": "The lab stopped this evaluation because of an unexpected server error."})
    finally:
        save(ev)


class _Stop(Exception):
    pass


def _explain(cls: str, exc: NegotiationAborted, aid: str, label: str) -> dict:
    """An aborted trial in words. Infrastructure, not strategy: never an outcome."""
    if cls == "budget_exhausted":
        return {"title": "Cost cap reached",
                "message": f"The next call could exceed the ${MAX_COST_USD:.2f} cap for one evaluation.", "detail": exc.detail}
    if cls == "agent_failure":
        return {"title": f"Agent {aid} did not answer",
                "message": f"{label}'s endpoint failed to reply (network error, timeout or HTTP error). This is counted as "
                           f"a failure of Agent {aid}, not as a negotiation outcome, and its block is excluded.",
                "detail": exc.detail}
    return {"title": f"{label} (provider) did not answer",
            "message": "The model provider failed to reply. This is an infrastructure failure, not attributed to the "
                       "agent's strategy; its block is excluded.", "detail": exc.detail}


def _finish(ev: Evaluation, status: str, error: Optional[dict] = None) -> None:
    with ev._lock:
        ev.status, ev.error, ev.current = status, error, None


# ---------------------------------------------------------------- summary

def dist(values) -> dict:
    """Descriptive distribution; quartiles only once there are enough observations."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return {"n": 0}
    out = {"n": len(xs), "mean": statistics.fmean(xs), "median": statistics.median(xs), "min": xs[0], "max": xs[-1]}
    if len(xs) >= MIN_SPREAD_N:
        out["q1"], _, out["q3"] = statistics.quantiles(xs, n=4, method="inclusive")
    else:
        out["spread"] = "insufficient observations"
    return out


def blocks_report(trials: list[dict], planned_blocks: int) -> dict:
    by_block: dict[int, list] = {}
    for t in trials:
        by_block.setdefault(t["block"], []).append(t)
    complete, excluded = [], []
    for b, ts in sorted(by_block.items()):
        if len(ts) == len(CELLS) and all(t["status"] == "finished" for t in ts):
            complete.append(b)
            continue
        reasons = sorted({t["abort"]["class"] for t in ts if t["status"] == "aborted"})
        if len(ts) < len(CELLS):
            reasons.append("not all 4 negotiations ran (evaluation stopped)")
        excluded.append({"block": b, "reasons": reasons})
    return {"planned": planned_blocks, "complete": complete, "excluded": excluded,
            "not_run": planned_blocks - len(by_block)}


def summarize(trials: list[dict], planned_blocks: int) -> dict:
    """Aggregates over COMPLETE blocks only; failures are counted over every attempted trial."""
    blocks = blocks_report(trials, planned_blocks)
    done = [t for t in trials if t["block"] in blocks["complete"]]
    n = len(done)
    rate = lambda f: (sum(1 for t in done if f(t)) / n) if n else None  # noqa: E731
    agreed = [t for t in done if t["outcome"] == "agreed"]
    aborted = [t for t in trials if t["status"] == "aborted"]
    failures = {}
    for aid in "XY":
        own = [t for t in aborted if t["abort"]["class"] == "agent_failure" and t["abort"]["agent"] == aid]
        failures[aid] = {"attempted": len(trials), "agent_failures": len(own),
                         "rate": len(own) / len(trials) if trials else None,
                         "by_seat": {s: sum(1 for t in own if t["abort"]["seat"] == s) for s in "AB"}}
    per_agent = {}
    for aid in "XY":
        rows = [t["per_agent"][aid] for t in done]
        per_agent[aid] = {
            "utility": dist(r["utility"] for r in rows),
            "utility_by_seat": {s: dist(r["utility"] for r in rows if r["seat"] == s) for s in "AB"},
            "walk_aways": sum(r["walked_away"] for r in rows), "invalid_actions": sum(r["invalid"] for r in rows),
            **{m: dist(r[m] for r in rows) for m in ("opening_demand", "total_concession",
                                                    "concession_frequency", "accepted_value")},
        }
    return {"blocks": blocks, "used": n, "attempted": len(trials),
            "finished": sum(t["status"] == "finished" for t in trials), "aborted": len(aborted),
            "aborts_by_class": {c: sum(t["abort"]["class"] == c for t in aborted)
                                for c in ("agent_failure", "provider_failure", "budget_exhausted")},
            "failures": failures, "exclusion_warning": bool(blocks["excluded"]),
            "outcomes": {o: rate(lambda t, o=o: t["outcome"] == o) for o in ("agreed", "walked_away", "timeout", "invalid_action")},
            "rounds": dist(t["rounds"] for t in done), "rwe": dist(t["rwe"] for t in done),
            "ew": dist(t["ew"] for t in done), "equitability": dist(t["equitability"] for t in agreed),
            "envy_free_rate": (sum(bool(t["envy_free"]) for t in agreed) / len(agreed)) if agreed else None,
            "cell_balance": {c: dist(t["optimal_welfare"] for t in done if t["cell"] == c) for c in CELLS},
            "per_agent": per_agent}


# ---------------------------------------------------------------- store and sealed API

_active: Optional[Evaluation] = None  # ponytail: one evaluation at a time bounds cost; a queue if several users share a server
_evals: dict[str, Evaluation] = {}
_guard = threading.Lock()


def sealed() -> bool:
    """True while any evaluation runs: then no evaluation's private data is served."""
    return _active is not None


def start(req, background: bool = True) -> Evaluation:
    global _active
    key = _secret()
    cfg, tokens = validate(req)
    with _guard:
        if _active is not None:
            raise adapters.SpecError("Another evaluation is still running. Wait for it to finish.")
        ev = _active = Evaluation(cfg, key)
        _evals[ev.id] = ev

    def go():
        global _active
        try:
            execute(ev, key, tokens)
        finally:
            with _guard:
                _active = None

    threading.Thread(target=go, daemon=True).start() if background else go()
    return ev


def save(ev: Evaluation) -> None:
    LAB_DIR.mkdir(parents=True, exist_ok=True)
    (LAB_DIR / f"{ev.id}.json").write_text(json.dumps(ev.view(), default=str), encoding="utf-8")


def _load(eid: str) -> Optional[dict]:
    if eid in _evals:
        return _evals[eid].view()
    if re.fullmatch(r"[0-9a-f]{12}", eid) and (path := LAB_DIR / f"{eid}.json").exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _header(v: dict) -> dict:
    """Fields any caller may see at any time: no URLs, config, ids of keys or sets, or results."""
    ts, sc = v.get("trials") or [], v["config"].get("scenario", {})
    return {"kind": "agent-lab", "exploratory": True, "id": v["id"], "created": v["created"], "status": v["status"],
            "planned": v["planned"], "blocks": sc.get("blocks"), "categories": sc.get("categories"),
            "strategy": v["config"].get("strategy"),
            "agents": {aid: {"kind": a["kind"], "label": a["label"]} for aid, a in v["agents"].items()},
            "finished": sum(t["status"] == "finished" for t in ts), "aborted": sum(t["status"] == "aborted" for t in ts)}


def get(eid: str) -> Optional[dict]:
    v = _load(eid)
    if v is None or not sealed():
        return v
    out = {**_header(v), "sealed": True}
    if _active is not None and eid == _active.id:  # its own progress: what the agents already see
        out.update(current=v["current"], trials=[{k: t.get(k) for k in PUBLIC_TRIAL_FIELDS} for t in v["trials"]])
    return out


def listing() -> list[dict]:
    """Evaluations, newest first: headers only, and results only while nothing runs."""
    views = {e.id: e.view() for e in _evals.values()}
    if LAB_DIR.exists():
        for p in LAB_DIR.glob("*.json"):
            if p.stem not in views:
                try:
                    views[p.stem] = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
    is_sealed = sealed()
    out = []
    for v in views.values():
        row = {**_header(v), "sealed": is_sealed}
        if not is_sealed and v.get("summary"):
            row.update(agreed=v["summary"]["outcomes"]["agreed"], complete_blocks=len(v["summary"]["blocks"]["complete"]))
        out.append(row)
    return sorted(out, key=lambda v: v["created"], reverse=True)
