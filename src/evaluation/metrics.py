"""Metric calculations. See docs/spec.md §5.

Every function here takes evaluator-side data (NegotiationRecord, which
includes the hidden valuations) — never anything agents produced about
their own score. Agents are never asked to self-report metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.environment.allocation import compute_utility
from src.negotiation.protocol import NegotiationRecord


def has_valid_allocation(record: NegotiationRecord) -> bool:
    """Allocation-dependent metrics are defined only for agreed
    negotiations with a final allocation. Everything else (walk-away,
    timeout, invalid action) has no allocation, so those metrics are None
    rather than a misleading 0."""
    return record.outcome == "agreed" and record.final_allocation is not None


def social_welfare(record: NegotiationRecord) -> float | None:
    return record.total_welfare if has_valid_allocation(record) else None


def social_welfare_efficiency(record: NegotiationRecord) -> float | None:
    """spec §5.2. None if there is no valid allocation; 0 if optimal_welfare
    is 0 (degenerate instance)."""
    if not has_valid_allocation(record):
        return None
    if record.optimal_welfare <= 0:
        return 0.0
    return record.total_welfare / record.optimal_welfare


def relative_welfare_efficiency(record: NegotiationRecord) -> float:
    """spec §8.6, confirmatory. Unconditional: SW / optimal_welfare for an
    agreed negotiation with a valid allocation, 0 for every failure
    (walked_away, timeout, invalid_action)."""
    swe = social_welfare_efficiency(record)
    return 0.0 if swe is None else swe


def egalitarian_welfare(record: NegotiationRecord, total_points: float = 100.0) -> float:
    """spec §8.6, confirmatory. Unconditional: min(utility_A, utility_B) /
    100 for an agreed negotiation with a valid allocation, 0 for every
    failure."""
    if not has_valid_allocation(record):
        return 0.0
    return min(record.utility_A, record.utility_B) / total_points


def equitability(record: NegotiationRecord, total_points: float = 100.0) -> float | None:
    """spec §5.3. 1.0 = perfectly equitable, 0.0 = maximally unequal.
    None if there is no valid allocation."""
    if not has_valid_allocation(record):
        return None
    norm_a = record.utility_A / total_points
    norm_b = record.utility_B / total_points
    return 1.0 - abs(norm_a - norm_b)


def _utility_of_seat(record: NegotiationRecord, seat: str) -> float:
    return {"A": record.utility_A, "B": record.utility_B}[seat]


def model_imbalance(record: NegotiationRecord, model_x: str, model_y: str,
                    total_points: float = 100.0) -> float | None:
    """spec §8.6 diagnostic: u_x/100 - u_y/100 by MODEL, whichever seat each
    occupies. For the preregistered design pass (claude_name, gpt_name) to
    get u_Claude/100 - u_GPT/100. None if there is no valid allocation."""
    seats = {record.model_A: "A", record.model_B: "B"}
    if record.model_A == record.model_B or set(seats) != {model_x, model_y}:
        raise ValueError(f"Models {model_x!r}/{model_y!r} do not match the record's seats "
                         f"A={record.model_A!r}, B={record.model_B!r}")
    if not has_valid_allocation(record):
        return None
    return (_utility_of_seat(record, seats[model_x])
            - _utility_of_seat(record, seats[model_y])) / total_points


def first_mover_gap(record: NegotiationRecord, total_points: float = 100.0) -> float | None:
    """spec §8.6 diagnostic: u_first/100 - u_second/100, with first/second
    taken from record.first_mover. None if there is no valid allocation."""
    if record.first_mover not in ("A", "B"):
        raise ValueError(f"Unknown first_mover {record.first_mover!r}")
    if not has_valid_allocation(record):
        return None
    second = "B" if record.first_mover == "A" else "A"
    return (_utility_of_seat(record, record.first_mover)
            - _utility_of_seat(record, second)) / total_points


@dataclass(frozen=True)
class EnvyResult:
    envious_A: bool
    envious_B: bool

    @property
    def envy_free(self) -> bool:
        return not (self.envious_A or self.envious_B)


def envy_freeness(record: NegotiationRecord) -> EnvyResult | None:
    """spec §5.4. None if there is no valid allocation."""
    if not has_valid_allocation(record):
        return None

    bundle_A = record.final_allocation["A"]
    bundle_B = record.final_allocation["B"]

    value_A_of_own = compute_utility(bundle_A, record.valuation_A)
    value_A_of_other = compute_utility(bundle_B, record.valuation_A)
    value_B_of_own = compute_utility(bundle_B, record.valuation_B)
    value_B_of_other = compute_utility(bundle_A, record.valuation_B)

    return EnvyResult(
        envious_A=value_A_of_other > value_A_of_own,
        envious_B=value_B_of_other > value_B_of_own,
    )


@dataclass(frozen=True)
class BatchMetrics:
    n: int
    agreement_rate: float
    walked_away_rate: float
    timeout_rate: float
    invalid_action_rate: float
    mean_rounds_to_agreement: float | None  # None if no agreements at all
    # Unconditional confirmatory metrics (spec §8.6): mean over ALL n,
    # failures count as 0.
    mean_rwe: float
    mean_egalitarian_welfare: float
    # Allocation-dependent metrics: averaged over AGREED negotiations only,
    # None if there are none. The four outcome rates above use all n.
    mean_social_welfare: float | None
    mean_swe: float | None
    mean_equitability: float | None
    envy_free_rate: float | None
    imbalance_model_A_minus_B: float | None  # mean(norm_utility_A - norm_utility_B)


def summarize_batch(records: Iterable[NegotiationRecord]) -> BatchMetrics:
    """spec §5.5, §5.6. Aggregates a batch of NegotiationRecords.

    NOTE on §5.6 (exploitability/imbalance): this function reports
    imbalance_model_A_minus_B for whatever pairing produced the batch. To
    separate "model skill" from "first-mover advantage" per spec §5.6, run
    this once on a role-swapped batch (same model pairing, first_mover
    flipped) and compare — that comparison is a Phase-2+ analysis step,
    not something this function does on its own.
    """
    records = list(records)
    n = len(records)
    if n == 0:
        raise ValueError("summarize_batch called with an empty batch")

    agreed = [r for r in records if r.outcome == "agreed"]
    walked = [r for r in records if r.outcome == "walked_away"]
    timed_out = [r for r in records if r.outcome == "timeout"]
    invalid = [r for r in records if r.outcome == "invalid_action"]

    def mean(xs):
        return sum(xs) / len(xs) if xs else None

    envy_results = [envy_freeness(r) for r in agreed]

    return BatchMetrics(
        n=n,
        agreement_rate=len(agreed) / n,
        walked_away_rate=len(walked) / n,
        timeout_rate=len(timed_out) / n,
        invalid_action_rate=len(invalid) / n,
        mean_rounds_to_agreement=(
            sum(r.num_rounds for r in agreed) / len(agreed) if agreed else None
        ),
        mean_rwe=mean([relative_welfare_efficiency(r) for r in records]),
        mean_egalitarian_welfare=mean([egalitarian_welfare(r) for r in records]),
        mean_social_welfare=mean([social_welfare(r) for r in agreed]),
        mean_swe=mean([social_welfare_efficiency(r) for r in agreed]),
        mean_equitability=mean([equitability(r) for r in agreed]),
        envy_free_rate=mean([1.0 if e.envy_free else 0.0 for e in envy_results]),
        imbalance_model_A_minus_B=mean([(r.utility_A - r.utility_B) / 100.0 for r in agreed]),
    )
