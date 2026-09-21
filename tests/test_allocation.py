import pytest

from src.environment.allocation import (
    Allocation,
    InvalidAllocationError,
    compute_utility,
    validate_allocation,
)


def test_valid_allocation_passes():
    pool = {"widgets": 10, "gadgets": 20}
    alloc = Allocation(a_A={"widgets": 6, "gadgets": 5}, a_B={"widgets": 4, "gadgets": 15})
    validate_allocation(alloc, pool)  # should not raise


def test_over_allocation_rejected():
    pool = {"widgets": 10}
    alloc = Allocation(a_A={"widgets": 7}, a_B={"widgets": 7})  # 14 != 10
    with pytest.raises(InvalidAllocationError):
        validate_allocation(alloc, pool)


def test_under_allocation_rejected():
    pool = {"widgets": 10}
    alloc = Allocation(a_A={"widgets": 3}, a_B={"widgets": 3})  # 6 != 10, leftover
    with pytest.raises(InvalidAllocationError):
        validate_allocation(alloc, pool)


def test_negative_allocation_rejected():
    pool = {"widgets": 10}
    alloc = Allocation(a_A={"widgets": -1}, a_B={"widgets": 11})
    with pytest.raises(InvalidAllocationError):
        validate_allocation(alloc, pool)


def test_missing_category_rejected():
    pool = {"widgets": 10, "gadgets": 5}
    alloc = Allocation(a_A={"widgets": 10}, a_B={"widgets": 0})  # gadgets missing
    with pytest.raises(InvalidAllocationError):
        validate_allocation(alloc, pool)


def test_unexpected_category_rejected():
    pool = {"widgets": 10}
    alloc = Allocation(a_A={"widgets": 5, "ghost": 1}, a_B={"widgets": 5, "ghost": -1})
    with pytest.raises(InvalidAllocationError):
        validate_allocation(alloc, pool)


def test_compute_utility_hand_example():
    bundle = {"widgets": 4, "gadgets": 2}
    valuation = {"widgets": 1.5, "gadgets": 3.0}
    # 4*1.5 + 2*3.0 = 6 + 6 = 12
    assert compute_utility(bundle, valuation) == 12.0


def test_compute_utility_empty_bundle_is_zero():
    assert compute_utility({}, {"widgets": 5.0}) == 0.0
