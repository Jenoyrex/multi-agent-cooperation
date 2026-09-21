"""Private per-agent valuation generation. See docs/spec.md §2.

Valuations are generated from a Dirichlet draw over a fixed point budget
(TOTAL_POINTS) so that every agent's maximum possible utility (owning 100%
of every category) is identical and known in advance. That fixed ceiling is
what makes the fairness metric (equitability) comparable across agents and
across different negotiation instances.

We avoid a numpy dependency for something this small: a Dirichlet(alpha,...)
draw is obtained by drawing K independent Gamma(alpha, 1) samples and
normalizing, which `random.gammavariate` supports directly.
"""
from __future__ import annotations

import hashlib
import random
from typing import Dict

TOTAL_POINTS = 100
DEFAULT_ALPHA = 1.0  # uniform over the simplex


def derive_seed(*parts) -> int:
    """Process-independent seed from arbitrary parts. Python's built-in
    hash() is salted per process for str, so it must not seed experiments."""
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _dirichlet(rng: random.Random, k: int, alpha: float) -> list[float]:
    if k == 1:
        return [1.0]
    samples = [rng.gammavariate(alpha, 1.0) for _ in range(k)]
    total = sum(samples)
    if total == 0:
        # Degenerate case (astronomically unlikely with alpha=1.0); fall
        # back to a uniform split rather than dividing by zero.
        return [1.0 / k] * k
    return [s / total for s in samples]


def generate_valuation(
    instance_seed: int,
    agent_role: str,
    resource_pool: Dict[str, int],
    total_points: int = TOTAL_POINTS,
    alpha: float = DEFAULT_ALPHA,
) -> Dict[str, float]:
    """Generate a private per-unit valuation vector for one agent.

    Deterministic given (instance_seed, agent_role, resource_pool, alpha).
    Different agent_role values (e.g. "A" vs "B") under the same
    instance_seed yield independent draws, not the same vector.

    Returns: category -> value_per_unit, such that
        sum(value_per_unit[c] * quantity[c] for c in categories) == total_points
    (up to floating point rounding).
    """
    # Mix the instance seed with the agent role so A and B diverge even
    # under the same instance_seed, while remaining reproducible.
    role_seed = derive_seed(instance_seed, agent_role)
    rng = random.Random(role_seed)

    categories = list(resource_pool.keys())
    shares = _dirichlet(rng, len(categories), alpha)

    valuation: Dict[str, float] = {}
    for cat, share in zip(categories, shares):
        qty = resource_pool[cat]
        points_for_cat = share * total_points
        valuation[cat] = points_for_cat / qty if qty > 0 else 0.0
    return valuation


def max_possible_utility(total_points: int = TOTAL_POINTS) -> float:
    """The utility an agent would realize if it received 100% of every
    category. By construction this equals total_points for every agent,
    which is exactly what makes normalized-utility comparisons valid."""
    return float(total_points)
