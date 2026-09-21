"""Ground-truth optimal social welfare. See docs/spec.md §4.

Computed once per negotiation instance from both hidden valuations,
completely independently of anything the agents do or say. Never exposed
to either agent during the negotiation.
"""
from __future__ import annotations

from typing import Dict, Tuple

from src.environment.allocation import Allocation


def compute_optimal_welfare(
    resource_pool: Dict[str, int],
    valuation_A: Dict[str, float],
    valuation_B: Dict[str, float],
) -> Tuple[Allocation, float]:
    """Closed-form optimum under linear, separable valuations (spec §1.3,
    §4): give each category's units entirely to whichever agent values
    that category more. Ties go to A arbitrarily (does not affect the
    welfare value, only who nominally receives tied categories).

    Returns (optimal_allocation, optimal_welfare).
    """
    a_A: Dict[str, int] = {}
    a_B: Dict[str, int] = {}
    welfare = 0.0

    for cat, qty in resource_pool.items():
        v_a = valuation_A.get(cat, 0.0)
        v_b = valuation_B.get(cat, 0.0)
        if v_a >= v_b:
            a_A[cat] = qty
            a_B[cat] = 0
            welfare += qty * v_a
        else:
            a_A[cat] = 0
            a_B[cat] = qty
            welfare += qty * v_b

    return Allocation(a_A=a_A, a_B=a_B), welfare
