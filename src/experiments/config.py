"""Experiment configuration. See docs/spec.md §Cost Control.

Three modes, deliberately kept separate in code (not just by a number
parameter) so a "full" run can never happen by accident:

  SMOKE  - tiny, cheap, for verifying the harness itself. Safe to run
           with MockAgent at zero cost, or with 1-2 real-model calls.
  PILOT  - small real sample (a handful of negotiations per condition),
           used to sanity-check that real-model behavior looks reasonable
           before committing to a full run. Requires a hard budget.
  FULL   - the actual experiment. Requires print_cost_estimate() to have
           been shown, an explicit `confirmed=True` passed in, and a hard
           budget.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

from src.negotiation.usage import Prices, call_cost

Mode = Literal["smoke", "pilot", "full"]

# Used only when no observed usage is supplied; a labeled rough assumption
# (system prompt + a transcript that grows over the negotiation).
ASSUMED_INPUT_TOKENS_PER_CALL = 1500

# ---- Frozen runtime configuration (docs/spec.md §8.15) -------------------
# Enforced for every pilot/full run by check_approved_runtime.
#
# Scientific: shapes model behavior, so it is part of the preregistration.
APPROVED_SCIENTIFIC = {
    "ClaudeAgent": {"model": "claude-sonnet-4-6", "temperature": 1.0,
                    "max_output_tokens": 1024, "effort": "medium"},
    "OpenAIAgent": {"model": "gpt-4.1-2025-04-14", "temperature": 1.0,
                    "max_output_tokens": 1024, "effort": None},
}
APPROVED_CLAUDE_THINKING = "disabled"
APPROVED_MAX_ROUNDS = 10
# Operational: affects only infrastructure failures, never strategic outcomes.
APPROVED_OPERATIONAL = {"timeout_s": 120.0, "max_retries": 3, "retry_backoff_s": 2.0}
APPROVED_MAX_TRANSPORT_RERUNS = 2
# USD per 1M (input, output) tokens for the approved models, as supplied by
# the user from provider pricing (Standard tier). Used for dollar caps.
APPROVED_PRICES = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "gpt-4.1-2025-04-14": (2.0, 8.0),
}


def check_approved_runtime(config: "ExperimentConfig", agents) -> None:
    """Raise ValueError unless the run uses exactly the frozen runtime
    configuration: one Claude and one OpenAI agent with the approved
    generation settings, and the approved round limit and rerun count."""
    problems = []
    if config.max_rounds != APPROVED_MAX_ROUNDS:
        problems.append(f"max_rounds={config.max_rounds} (approved {APPROVED_MAX_ROUNDS})")
    if config.max_transport_reruns != APPROVED_MAX_TRANSPORT_RERUNS:
        problems.append(f"max_transport_reruns={config.max_transport_reruns} "
                        f"(approved {APPROVED_MAX_TRANSPORT_RERUNS})")
    kinds = sorted(type(a).__name__ for a in agents)
    if kinds != sorted(APPROVED_SCIENTIFIC):
        problems.append(f"agents {kinds} (approved one ClaudeAgent and one OpenAIAgent)")
    for agent in agents:
        approved = APPROVED_SCIENTIFIC.get(type(agent).__name__)
        if approved is None:
            continue
        for field, want in {**approved, **APPROVED_OPERATIONAL}.items():
            got = getattr(agent.config, field)
            if got != want:
                problems.append(f"{agent.name}: {field}={got!r} (approved {want!r})")
        if type(agent).__name__ == "ClaudeAgent" and agent.thinking != APPROVED_CLAUDE_THINKING:
            problems.append(f"{agent.name}: thinking={agent.thinking!r} "
                            f"(approved {APPROVED_CLAUDE_THINKING!r})")
    if problems:
        raise ValueError(f"{config.mode} run does not match the approved runtime "
                         f"configuration: " + "; ".join(problems))


@dataclass(frozen=True)
class ExperimentConfig:
    mode: Mode
    method: str  # e.g. "baseline_plain_prompting"
    model_A_label: str
    model_B_label: str
    num_negotiations: int
    base_seed: int
    max_rounds: int = 10
    num_categories: int = 3
    # Who moves first, chosen independently of instance_seed/valuations:
    # "A" or "B" (fixed), or "alternate" (by negotiation index, A first).
    first_mover_policy: Literal["A", "B", "alternate"] = "alternate"
    # Infrastructure failures (transport errors) never count as outcomes.
    # If > 0, a negotiation that aborted on a transport failure is re-run
    # from scratch (never resumed) up to this many extra times.
    max_transport_reruns: int = 0
    # Hard run budget (checked before every agent call). At least one is
    # required for pilot/full mode. A dollar budget needs a price table.
    budget_max_total_tokens: Optional[int] = None
    budget_max_cost_usd: Optional[float] = None
    # Explicit per-attempt input-token ceiling used to reserve the worst
    # case of each call before it starts (see Budget). Required for pilot
    # mode; no default, because input size is only known after a call.
    budget_max_input_tokens_per_call: Optional[int] = None

    def first_mover_for(self, index: int) -> str:
        if self.first_mover_policy == "alternate":
            return "A" if index % 2 == 0 else "B"
        return self.first_mover_policy

    def __post_init__(self):
        if self.max_rounds < 2:
            raise ValueError(
                "max_rounds must be >= 2: the final turn is response-only, so a "
                "1-round negotiation could never produce an agreement."
            )
        if self.first_mover_policy not in ("A", "B", "alternate"):
            raise ValueError(f"Unknown first_mover_policy: {self.first_mover_policy!r}")
        if self.max_transport_reruns < 0:
            raise ValueError("max_transport_reruns must be >= 0")
        for name in ("budget_max_total_tokens", "budget_max_cost_usd",
                     "budget_max_input_tokens_per_call"):
            v = getattr(self, name)
            if v is not None and (isinstance(v, bool) or not math.isfinite(v) or v <= 0):
                raise ValueError(f"{name} must be a finite number > 0")
        if self.mode == "pilot" and self.budget_max_input_tokens_per_call is None:
            raise ValueError("pilot mode requires budget_max_input_tokens_per_call "
                             "(the per-call reserve of the budget guard).")
        if self.mode == "smoke" and self.num_negotiations > 5:
            raise ValueError("Smoke mode is capped at 5 negotiations by design.")
        if self.mode == "pilot" and self.num_negotiations > 20:
            raise ValueError("Pilot mode is capped at 20 negotiations by design.")
        if self.mode in ("pilot", "full") and (
            self.budget_max_total_tokens is None and self.budget_max_cost_usd is None
        ):
            raise ValueError(f"{self.mode} mode requires budget_max_total_tokens and/or budget_max_cost_usd.")


def observed_usage(conn, run_id: Optional[str] = None) -> Optional[dict]:
    """Average tokens per API call actually observed in stored negotiations
    (optionally one run). None if no call has reported usage."""
    where, args = ("WHERE run_id = ?", (run_id,)) if run_id else ("", ())
    row = conn.execute(
        f"SELECT SUM(api_calls), SUM(input_tokens), SUM(output_tokens) FROM negotiations {where}", args
    ).fetchone()
    calls, tin, tout = row
    if not calls or tin is None or tout is None:
        return None
    return {"avg_input_tokens_per_call": tin / calls, "avg_output_tokens_per_call": tout / calls}


def estimate_cost(
    config: ExperimentConfig,
    generation_configs: Optional[dict] = None,
    prices: Optional[Prices] = None,
    observed: Optional[dict] = None,
) -> dict:
    """Upper-bound projection, explicitly labeled as an estimate.

    Calls: every negotiation using every round (each agent takes
    ceil(max_rounds/2) turns). Output: `max_output_tokens` per call when
    generation configs ({"A": cfg, "B": cfg}) are given (a hard ceiling),
    else the observed average, else unknown. Input: the observed average
    per call when `observed` (see observed_usage) is given, otherwise a
    labeled assumption. Dollars only when prices cover the models used."""
    per_agent_calls = config.num_negotiations * math.ceil(config.max_rounds / 2)
    basis = "observed" if observed else "assumed"
    avg_in = observed["avg_input_tokens_per_call"] if observed else ASSUMED_INPUT_TOKENS_PER_CALL

    total_in = total_out = 0.0
    cost: Optional[float] = 0.0
    out_known = True
    for role in ("A", "B"):
        gen = (generation_configs or {}).get(role)
        if gen is not None:
            avg_out = gen.max_output_tokens
        elif observed:
            avg_out = observed["avg_output_tokens_per_call"]
        else:
            out_known = False
            avg_out = 0
        total_in += per_agent_calls * avg_in
        total_out += per_agent_calls * avg_out
        c = call_cost(
            {"model": gen.model if gen else None,
             "input_tokens": per_agent_calls * avg_in, "output_tokens": per_agent_calls * avg_out},
            prices,
        )
        cost = None if (c is None or cost is None) else cost + c

    return {
        "num_negotiations": config.num_negotiations,
        "max_rounds_per_negotiation": config.max_rounds,
        "estimated_api_calls_upper_bound": 2 * per_agent_calls,
        "estimated_input_tokens_upper_bound": int(total_in),
        "estimated_output_tokens_upper_bound": int(total_out) if out_known else None,
        "estimated_tokens_upper_bound": int(total_in + total_out) if out_known else None,
        "estimated_cost_usd_upper_bound": cost if out_known else None,
        "input_basis": basis,
        "note": (
            "Upper bound assuming every negotiation runs the full round limit. "
            f"Input tokens use the {basis} per-call average. Not a billing figure; "
            "retries and failed calls are not included."
        ),
    }
