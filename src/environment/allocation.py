"""Allocation representation, validation, and utility calculation.
See docs/spec.md §1.2, §5.1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


class InvalidAllocationError(ValueError):
    """Raised when an allocation does not satisfy the environment's
    hard constraints. Never let an agent-proposed allocation reach
    storage or scoring without passing through validate_allocation."""


@dataclass(frozen=True)
class Allocation:
    a_A: Dict[str, int]
    a_B: Dict[str, int]


def validate_allocation(allocation: Allocation, resource_pool: Dict[str, int]) -> None:
    """Raises InvalidAllocationError if the allocation violates any
    environment constraint. Silent (returns None) if valid.

    Constraints (spec §1.2):
      - every category in resource_pool must appear in both a_A and a_B
      - no negative allocations
      - a_A[c] + a_B[c] == resource_pool[c] for every category (full
        allocation required, no leftover, no over-allocation)
      - no categories outside resource_pool may appear
    """
    pool_categories = set(resource_pool.keys())
    a_categories = set(allocation.a_A.keys()) | set(allocation.a_B.keys())

    if a_categories != pool_categories:
        missing = pool_categories - a_categories
        extra = a_categories - pool_categories
        raise InvalidAllocationError(
            f"Category mismatch. Missing: {missing}, unexpected: {extra}"
        )

    for cat, qty in resource_pool.items():
        a = allocation.a_A.get(cat, None)
        b = allocation.a_B.get(cat, None)
        if a is None or b is None:
            raise InvalidAllocationError(f"Category '{cat}' missing from one side")
        if a < 0 or b < 0:
            raise InvalidAllocationError(f"Negative allocation in category '{cat}'")
        if a + b != qty:
            raise InvalidAllocationError(
                f"Category '{cat}': allocated {a}+{b}={a + b} != available {qty}"
            )


def compute_utility(bundle: Dict[str, int], valuation: Dict[str, float]) -> float:
    """utility = sum over categories of (units held * value per unit)."""
    return sum(bundle.get(cat, 0) * valuation.get(cat, 0.0) for cat in bundle)
