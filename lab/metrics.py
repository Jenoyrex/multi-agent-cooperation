"""Behavioral metrics (Agent Lab, layer 2). Definitions: docs/phase2-agent-lab-plan.md §8.2.

Computed by the evaluator AFTER a negotiation, from the stored public
transcript and the seat's own private valuation. Every value is a
normalized utility: points by that seat's own valuation / 100 (every
agent's maximum). None means "not observed", never 0.
"""
from __future__ import annotations

from typing import Optional

from src.environment.allocation import compute_utility
from src.environment.valuations import TOTAL_POINTS
from src.negotiation.protocol import NegotiationRecord


def _valuation(record: NegotiationRecord, seat: str) -> dict:
    return record.valuation_A if seat == "A" else record.valuation_B


def own_offer_values(record: NegotiationRecord, seat: str) -> list[float]:
    """u_own of each offer the seat made, in order (including a final-round offer)."""
    v = _valuation(record, seat)
    return [compute_utility(t.action.allocation[seat], v) / TOTAL_POINTS
            for t in record.transcript
            if t.actor == seat and t.action.action_type == "OFFER" and t.action.allocation]


def opening_demand(record: NegotiationRecord, seat: str) -> Optional[float]:
    """u_own(first offer by the seat) / 100."""
    offers = own_offer_values(record, seat)
    return offers[0] if offers else None


def total_concession(record: NegotiationRecord, seat: str) -> Optional[float]:
    """(u_own(first own offer) - u_own(last own offer)) / 100. Negative = hardened."""
    offers = own_offer_values(record, seat)
    return offers[0] - offers[-1] if len(offers) >= 2 else None


def concession_frequency(record: NegotiationRecord, seat: str) -> Optional[float]:
    """Share of consecutive own-offer pairs in which the seat asked for less."""
    offers = own_offer_values(record, seat)
    if len(offers) < 2:
        return None
    return sum(b < a for a, b in zip(offers, offers[1:])) / (len(offers) - 1)


def accepted_value(record: NegotiationRecord, seat: str) -> Optional[float]:
    """u_own of the allocation the seat accepted; None unless this seat accepted."""
    last = record.transcript[-1] if record.transcript else None
    if record.outcome != "agreed" or not last or last.actor != seat or last.action.action_type != "ACCEPT":
        return None
    return compute_utility(record.final_allocation[seat], _valuation(record, seat)) / TOTAL_POINTS


def ended_by(record: NegotiationRecord) -> Optional[str]:
    """Seat whose turn ended a walk-away or an invalid action (frozen alternation rule)."""
    if record.outcome == "walked_away":
        return record.transcript[-1].actor
    if record.outcome == "invalid_action":  # the faulty turn is never in the transcript
        other = "B" if record.first_mover == "A" else "A"
        return record.first_mover if record.num_rounds % 2 == 1 else other
    return None


def behavior(record: NegotiationRecord, seat: str) -> dict:
    return {"offers": len(own_offer_values(record, seat)),
            "opening_demand": opening_demand(record, seat),
            "total_concession": total_concession(record, seat),
            "concession_frequency": concession_frequency(record, seat),
            "accepted_value": accepted_value(record, seat)}
