"""Pilot driver (docs/spec.md §8.4). Runs exactly the preregistered pilot:
3 seeds x 8 cells (condition x Claude seat x first mover) = 24 negotiations,
as 8 pilot-mode batches of 3, with the frozen runtime configuration (§8.15).

Everything is checked before the first API call: the budget values, a price
for both approved models, the condition label and the approved runtime of
every cell, and that one call's worst case fits in the budget. One Budget is
shared by all 8 batches, so the caps apply to the whole pilot.

Pilot data stays out of the confirmatory dataset: it goes to its own
database (which must not exist yet), every run is stored with mode='pilot',
and its seeds (30000-30002) are disjoint from the full run's (40000-40059).
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.agents.claude_agent import ClaudeAgent
from src.agents.generation import GenerationConfig
from src.agents.openai_agent import OpenAIAgent
from src.agents.prompting import INSTRUCTION_VARIANTS
from src.experiments.config import (APPROVED_MAX_ROUNDS, APPROVED_MAX_TRANSPORT_RERUNS,
                                    APPROVED_PRICES, ExperimentConfig, check_approved_runtime)
from src.experiments.runner import check_condition_label, make_budget, run_batch
from src.negotiation.usage import Budget, Prices

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


def pilot_matrix() -> List[tuple]:
    """The 24 (cell, seed) pairs the pilot runs, in run order."""
    return [(cell, seed) for cell in PILOT_CELLS for seed in PILOT_SEEDS]


def pilot_config(cell: PilotCell, max_total_tokens=None, max_cost_usd=None,
                 max_input_tokens_per_call=None) -> ExperimentConfig:
    return ExperimentConfig(
        mode="pilot",
        method=cell.condition,
        model_A_label="claude" if cell.claude_seat == "A" else "openai",
        model_B_label="openai" if cell.claude_seat == "A" else "claude",
        num_negotiations=len(PILOT_SEEDS),
        base_seed=PILOT_SEEDS[0],
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


def pilot_worst_case(budget: Budget) -> dict:
    """Upper bound for the planned pilot if every negotiation uses all
    rounds and every call its reserved worst case (transport reruns, which
    re-run whole negotiations, can add up to x(1 + max_transport_reruns))."""
    calls_per_model = len(pilot_matrix()) * math.ceil(APPROVED_MAX_ROUNDS / 2)
    tokens, cost = 0, 0.0
    for model, (max_in, max_out, attempts) in budget.reserve.items():
        tokens += calls_per_model * attempts * (max_in + max_out)
        price = (budget.prices or {}).get(model)
        cost += calls_per_model * attempts * (max_in * price[0] + max_out * price[1]) / 1e6 if price else math.nan
    return {"calls_per_model": calls_per_model, "worst_case_tokens": tokens, "worst_case_cost_usd": cost}


def prepare_pilot(claude_cfg: GenerationConfig, openai_cfg: GenerationConfig, db_path,
                  max_total_tokens=None, max_cost_usd=None, max_input_tokens_per_call=None,
                  prices: Optional[Prices] = APPROVED_PRICES, client_factory=None):
    """Validate everything and build (plan, shared budget). Raises ValueError
    before any API call if anything is missing, invalid or unapproved."""
    if Path(db_path).exists():
        raise ValueError(f"{db_path} already exists; the pilot must go to a new database.")
    if max_total_tokens is None and max_cost_usd is None:
        raise ValueError("The pilot requires a token cap and/or a dollar cap.")
    missing = sorted(m for m in (claude_cfg.model, openai_cfg.model) if not prices or m not in prices)
    if missing:
        raise ValueError(f"No price entry for models: {missing}")
    plan = []
    for cell in PILOT_CELLS:
        config = pilot_config(cell, max_total_tokens, max_cost_usd, max_input_tokens_per_call)
        agent_A, agent_B = pilot_agents(cell, claude_cfg, openai_cfg, client_factory)
        check_condition_label(config, [agent_A, agent_B])
        check_approved_runtime(config, [agent_A, agent_B])
        plan.append((cell, config, agent_A, agent_B))
    _, config, agent_A, agent_B = plan[0]
    budget = make_budget(config, [agent_A, agent_B], prices)
    why = budget.exceeded()
    if why:
        raise ValueError(f"Budget cannot cover even the first call: {why}")
    return plan, budget


async def run_pilot(claude_cfg: GenerationConfig, openai_cfg: GenerationConfig, db_path,
                    max_total_tokens=None, max_cost_usd=None, max_input_tokens_per_call=None,
                    prices: Optional[Prices] = APPROVED_PRICES, client_factory=None) -> dict:
    """Run the 8 pilot batches in order with one shared budget. Stops at the
    first batch that does not complete all its negotiations (budget stop or
    exhausted transport reruns); that batch's run row records why."""
    plan, budget = prepare_pilot(claude_cfg, openai_cfg, db_path, max_total_tokens, max_cost_usd,
                                 max_input_tokens_per_call, prices, client_factory)
    batches = []
    status = "completed"
    for cell, config, agent_A, agent_B in plan:
        records = await run_batch(config, agent_A, agent_B, db_path, prices=prices, budget=budget)
        batches.append({"cell": cell, "negotiations": len(records)})
        if len(records) != config.num_negotiations:
            status = "stopped"
            break
    return {"status": status, "batches": batches, "spent_tokens": budget.spent_tokens,
            "spent_cost_usd": budget.spent_cost_usd}
