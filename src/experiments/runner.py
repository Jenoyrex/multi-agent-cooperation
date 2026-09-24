"""Ties the environment, agents, protocol, and storage together into
runnable batches. See docs/spec.md §Cost Control, §First Pilot.
"""
from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path
from typing import List, Optional

from src.agents.base import Agent
from src.agents.prompting import BASELINE_VARIANT, INSTRUCTION_VARIANTS
from src.environment.optimum import compute_optimal_welfare
from src.environment.resources import generate_resource_pool
from src.environment.valuations import generate_valuation
from src.experiments import provenance
from src.experiments.config import ExperimentConfig, check_approved_runtime, estimate_cost
from src.negotiation.protocol import EvaluatorState, NegotiationAborted, NegotiationRecord, NegotiationSession
from src.negotiation.usage import Budget, Prices
from src.storage.db import finish_run, get_connection, run_totals, save_aborted, save_record, save_run


def _build_instance_state(
    instance_seed: int,
    config: ExperimentConfig,
    agent_A: Agent,
    agent_B: Agent,
    index: int = 0,
    run_id: Optional[str] = None,
) -> EvaluatorState:
    resource_pool = generate_resource_pool(seed=instance_seed, num_categories=config.num_categories)
    valuation_A = generate_valuation(instance_seed, "A", resource_pool)
    valuation_B = generate_valuation(instance_seed, "B", resource_pool)
    optimal_allocation, optimal_welfare = compute_optimal_welfare(resource_pool, valuation_A, valuation_B)

    # First mover comes from config policy + negotiation index only; it
    # never influences (or is influenced by) the resource/valuation draw.
    first_mover = config.first_mover_for(index)

    return EvaluatorState(
        instance_seed=instance_seed,
        resource_pool=resource_pool,
        valuation_A=valuation_A,
        valuation_B=valuation_B,
        optimal_allocation=optimal_allocation,
        optimal_welfare=optimal_welfare,
        max_rounds=config.max_rounds,
        first_mover=first_mover,
        first_mover_policy=config.first_mover_policy,
        method=config.method,
        model_A_name=agent_A.name,
        model_B_name=agent_B.name,
        run_id=run_id,
        negotiation_index=index,
    )


def _describe_agent(agent: Agent) -> dict:
    """Name, class, generation settings (real agents) and any primitive
    constructor parameters (e.g. MockAgent's), so a run's agents can be
    reconstructed from the stored config."""
    params = {
        k: v for k, v in vars(agent).items()
        if not k.startswith("_") and k not in ("config", "last_call", "name")
        and isinstance(v, (int, float, str, bool, type(None)))
    }
    return {
        "name": agent.name,
        "class": type(agent).__name__,
        "generation_config": agent.config.to_dict() if agent.config is not None else None,
        "params": params,
    }


def build_run_metadata(
    config: ExperimentConfig, agent_A: Agent, agent_B: Agent, prices: Optional[Prices]
) -> dict:
    return {
        "method": config.method,
        "mode": config.mode,
        "base_seed": config.base_seed,
        "num_negotiations": config.num_negotiations,
        "max_rounds": config.max_rounds,
        "num_categories": config.num_categories,
        "first_mover_policy": config.first_mover_policy,
        "protocol_id": provenance.PROTOCOL_ID,
        "spec_version": provenance.spec_version(),
        "prompt_hash": provenance.prompt_hash(),
        "code_version": provenance.code_version(),
        "dependency_versions": provenance.dependency_versions(),
        "config": {
            "experiment": dataclasses.asdict(config),
            "environment": provenance.environment_params(config.num_categories),
            "agents": {"A": _describe_agent(agent_A), "B": _describe_agent(agent_B)},
            "prices_usd_per_million_tokens": prices,
            # True iff check_approved_runtime was enforced (pilot/full modes).
            "approved_runtime_enforced": config.mode in ("pilot", "full"),
        },
    }


def check_condition_label(config: ExperimentConfig, agents: List[Agent]) -> None:
    """The run's condition label (config.method) must match the instructions
    each agent actually sends, so a run can never be labelled structured_v1
    while sending the baseline prompt (or vice versa). A prompt-sending agent
    is accepted only if the label names its variant, or if it sends the
    baseline under a label that names no variant (legacy labels such as
    "baseline_plain_prompting"). Mock agents send no prompt and are skipped."""
    for role, agent in zip("AB", agents):
        variant = getattr(agent, "instructions_variant", None)
        if variant is None:
            continue
        if config.method == variant:
            continue
        if variant == BASELINE_VARIANT and config.method not in INSTRUCTION_VARIANTS:
            continue
        raise ValueError(
            f"Condition label {config.method!r} does not match agent {role} "
            f"({agent.name}), which sends instruction variant {variant!r}."
        )


def _make_budget(config: ExperimentConfig, agents: List[Agent], prices: Optional[Prices]) -> Optional[Budget]:
    if config.budget_max_total_tokens is None and config.budget_max_cost_usd is None:
        return None
    if config.budget_max_cost_usd is not None:
        models = {a.config.model for a in agents if a.config is not None}
        missing = sorted(m for m in models if not prices or m not in prices)
        if missing:
            raise ValueError(f"budget_max_cost_usd needs prices for models: {missing}")
    return Budget(config.budget_max_total_tokens, config.budget_max_cost_usd, prices)


async def run_batch(
    config: ExperimentConfig,
    agent_A: Agent,
    agent_B: Agent,
    db_path: str | Path,
    confirmed_full_run: bool = False,
    prices: Optional[Prices] = None,
) -> List[NegotiationRecord]:
    """Run `config.num_negotiations` independent negotiations and persist
    each to SQLite as it completes (not batched at the end, so a crash
    partway through doesn't lose everything already run).

    Infrastructure failures are not outcomes: a negotiation aborted by a
    transport failure is logged in `aborted_negotiations` and, if
    config.max_transport_reruns allows, re-run from scratch (never resumed).
    If reruns are exhausted the negotiation is simply absent from
    `negotiations` and the run finishes as 'incomplete'. A budget stop ends
    the whole run as 'budget_exhausted'."""
    check_condition_label(config, [agent_A, agent_B])
    if config.mode in ("pilot", "full"):
        check_approved_runtime(config, [agent_A, agent_B])
    gen_configs ={"A": agent_A.config, "B": agent_B.config}
    if config.mode == "full" and not confirmed_full_run:
        print("=== FULL EXPERIMENT — COST ESTIMATE (approval required) ===")
        for k, v in estimate_cost(config, gen_configs, prices).items():
            print(f"  {k}: {v}")
        print("Re-run with confirmed_full_run=True to proceed. STOPPING.")
        return []

    budget = _make_budget(config, [agent_A, agent_B], prices)
    run_id = uuid.uuid4().hex
    conn = get_connection(db_path)
    records: List[NegotiationRecord] = []
    status = "completed"
    try:
        save_run(conn, run_id, build_run_metadata(config, agent_A, agent_B, prices))
        max_attempts = 1 + config.max_transport_reruns
        for i in range(config.num_negotiations):
            instance_seed = config.base_seed + i
            for attempt in range(1, max_attempts + 1):
                state = _build_instance_state(instance_seed, config, agent_A, agent_B, index=i, run_id=run_id)
                session = NegotiationSession(agent_A, agent_B, state, budget=budget, prices=prices)
                try:
                    record = await session.run()
                except NegotiationAborted as aborted:
                    will_rerun = aborted.reason == "transport_failure" and attempt < max_attempts
                    save_aborted(conn, run_id, i, attempt, aborted, will_rerun, prices)
                    if aborted.reason == "budget_exhausted":
                        status = "budget_exhausted"
                        break
                    if will_rerun:
                        continue
                    status = "incomplete"  # gave up on this negotiation
                    break
                save_record(conn, record)
                records.append(record)
                break
            if status == "budget_exhausted":
                break
    except BaseException:
        status = "failed"
        raise
    finally:
        finish_run(conn, run_id, status, run_totals(conn, run_id))
        conn.close()
    return records
