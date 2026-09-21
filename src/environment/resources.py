"""Resource pool generation. See docs/spec.md §1.1."""
from __future__ import annotations

import random
from typing import Dict

DEFAULT_NUM_CATEGORIES = 3
DEFAULT_CATEGORY_NAMES = ["widgets", "gadgets", "components"]
DEFAULT_MIN_QTY = 10
DEFAULT_MAX_QTY = 50


def generate_resource_pool(
    seed: int,
    num_categories: int = DEFAULT_NUM_CATEGORIES,
    min_qty: int = DEFAULT_MIN_QTY,
    max_qty: int = DEFAULT_MAX_QTY,
    category_names: list[str] | None = None,
) -> Dict[str, int]:
    """Deterministically generate a resource pool from a seed.

    Returns a mapping category_name -> integer quantity. Same seed +
    same params -> bit-identical output (uses a local Random instance,
    not the global RNG, so this is safe to call repeatedly without
    interference from other seeded draws in the same process).
    """
    if category_names is None:
        category_names = DEFAULT_CATEGORY_NAMES[:num_categories]
    if len(category_names) != num_categories:
        raise ValueError("category_names length must match num_categories")

    rng = random.Random(seed)
    return {name: rng.randint(min_qty, max_qty) for name in category_names}
