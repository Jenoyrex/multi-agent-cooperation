from src.evaluation.metrics import (
    envy_freeness,
    equitability,
    social_welfare,
    social_welfare_efficiency,
    summarize_batch,
)
from src.negotiation.protocol import NegotiationRecord


def _make_record(**overrides) -> NegotiationRecord:
    defaults = dict(
        instance_seed=1,
        method="baseline",
        model_A="mock-A",
        model_B="mock-B",
        first_mover="A",
        outcome="agreed",
        num_rounds=3,
        final_allocation={"A": {"widgets": 8, "gadgets": 2}, "B": {"widgets": 2, "gadgets": 8}},
        utility_A=50.0,
        utility_B=50.0,
        total_welfare=100.0,
        optimal_welfare=100.0,
        resource_pool={"widgets": 10, "gadgets": 10},
        valuation_A={"widgets": 5.0, "gadgets": 1.0},
        valuation_B={"widgets": 1.0, "gadgets": 5.0},
    )
    defaults.update(overrides)
    return NegotiationRecord(**defaults)


def test_swe_perfect_efficiency():
    record = _make_record(total_welfare=100.0, optimal_welfare=100.0)
    assert social_welfare_efficiency(record) == 1.0


def test_allocation_metrics_none_without_agreement():
    for outcome in ("timeout", "walked_away", "invalid_action"):
        record = _make_record(outcome=outcome, final_allocation=None,
                              utility_A=0.0, utility_B=0.0, total_welfare=0.0)
        assert social_welfare(record) is None
        assert social_welfare_efficiency(record) is None
        assert equitability(record) is None
        assert envy_freeness(record) is None


def test_allocation_metrics_none_when_agreed_but_allocation_missing():
    record = _make_record(outcome="agreed", final_allocation=None)
    assert social_welfare_efficiency(record) is None


def test_swe_handles_degenerate_zero_optimum():
    record = _make_record(total_welfare=0.0, optimal_welfare=0.0)
    assert social_welfare_efficiency(record) == 0.0


def test_equitability_perfectly_equal():
    record = _make_record(utility_A=50.0, utility_B=50.0)
    assert equitability(record) == 1.0


def test_equitability_maximally_unequal():
    record = _make_record(utility_A=100.0, utility_B=0.0)
    assert equitability(record) == 0.0


def test_equitability_hand_example():
    record = _make_record(utility_A=80.0, utility_B=60.0)
    # 1 - |0.8 - 0.6| = 1 - 0.2 = 0.8
    assert abs(equitability(record) - 0.8) < 1e-9


def test_envy_freeness_hand_example_no_envy():
    # A values widgets 5x, gadgets 1x. A got 8 widgets + 2 gadgets = 42.
    # If A had gotten B's bundle (2 widgets + 8 gadgets) = 10 + 8 = 18 < 42 -> not envious.
    record = _make_record(
        final_allocation={"A": {"widgets": 8, "gadgets": 2}, "B": {"widgets": 2, "gadgets": 8}},
        valuation_A={"widgets": 5.0, "gadgets": 1.0},
        valuation_B={"widgets": 1.0, "gadgets": 5.0},
    )
    result = envy_freeness(record)
    assert result.envy_free is True
    assert result.envious_A is False
    assert result.envious_B is False


def test_envy_freeness_hand_example_with_envy():
    # Now flip the allocation so A gets the bundle it likes LESS.
    record = _make_record(
        final_allocation={"A": {"widgets": 2, "gadgets": 8}, "B": {"widgets": 8, "gadgets": 2}},
        valuation_A={"widgets": 5.0, "gadgets": 1.0},
        valuation_B={"widgets": 1.0, "gadgets": 5.0},
    )
    result = envy_freeness(record)
    # A's own bundle value: 2*5 + 8*1 = 18. Value of B's bundle to A: 8*5 + 2*1 = 42. A envies.
    assert result.envious_A is True
    assert result.envy_free is False




def test_summarize_batch_basic():
    records = [
        _make_record(outcome="agreed", num_rounds=4, utility_A=60, utility_B=40, total_welfare=100, optimal_welfare=100),
        _make_record(outcome="agreed", num_rounds=6, utility_A=50, utility_B=50, total_welfare=100, optimal_welfare=100),
        _make_record(outcome="timeout", utility_A=0, utility_B=0, total_welfare=0, optimal_welfare=100, final_allocation=None),
    ]
    summary = summarize_batch(records)
    assert summary.n == 3
    assert abs(summary.agreement_rate - (2 / 3)) < 1e-9
    assert abs(summary.timeout_rate - (1 / 3)) < 1e-9
    assert summary.mean_rounds_to_agreement == 5.0  # mean of 4 and 6


def test_summarize_batch_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        summarize_batch([])


def test_summarize_batch_failures_do_not_dilute_allocation_metrics():
    records = [
        _make_record(outcome="agreed", utility_A=60, utility_B=40, total_welfare=100, optimal_welfare=100),
        _make_record(outcome="walked_away", final_allocation=None, utility_A=0, utility_B=0, total_welfare=0),
        _make_record(outcome="invalid_action", final_allocation=None, utility_A=0, utility_B=0, total_welfare=0),
        _make_record(outcome="timeout", final_allocation=None, utility_A=0, utility_B=0, total_welfare=0),
    ]
    m = summarize_batch(records)
    # Outcome rates use all 4 negotiations...
    assert (m.agreement_rate, m.walked_away_rate, m.timeout_rate, m.invalid_action_rate) == (0.25,) * 4
    # ...allocation metrics use only the 1 agreed one.
    assert m.mean_swe == 1.0
    assert m.mean_social_welfare == 100.0
    assert abs(m.mean_equitability - 0.8) < 1e-9
    assert m.envy_free_rate == 1.0


def test_summarize_batch_no_agreements_gives_none_not_zero():
    m = summarize_batch([_make_record(outcome="timeout", final_allocation=None)])
    assert m.agreement_rate == 0.0
    assert m.mean_swe is None and m.mean_equitability is None
    assert m.envy_free_rate is None and m.mean_social_welfare is None
