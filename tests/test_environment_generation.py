from src.environment.resources import generate_resource_pool
from src.environment.valuations import generate_valuation, TOTAL_POINTS


def test_resource_pool_deterministic():
    pool_1 = generate_resource_pool(seed=42)
    pool_2 = generate_resource_pool(seed=42)
    assert pool_1 == pool_2


def test_resource_pool_changes_with_seed():
    pool_1 = generate_resource_pool(seed=1)
    pool_2 = generate_resource_pool(seed=2)
    assert pool_1 != pool_2


def test_resource_pool_quantities_in_range():
    pool = generate_resource_pool(seed=7, min_qty=10, max_qty=50)
    for qty in pool.values():
        assert 10 <= qty <= 50


def test_valuation_deterministic():
    pool = generate_resource_pool(seed=5)
    v1 = generate_valuation(5, "A", pool)
    v2 = generate_valuation(5, "A", pool)
    assert v1 == v2


def test_valuation_differs_by_role():
    pool = generate_resource_pool(seed=5)
    v_a = generate_valuation(5, "A", pool)
    v_b = generate_valuation(5, "B", pool)
    assert v_a != v_b


def test_valuation_sums_to_total_points():
    pool = generate_resource_pool(seed=9)
    valuation = generate_valuation(9, "A", pool)
    total = sum(valuation[cat] * qty for cat, qty in pool.items())
    assert abs(total - TOTAL_POINTS) < 1e-6
