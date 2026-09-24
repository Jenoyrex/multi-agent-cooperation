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


# --- Unconditional confirmatory metrics: RWE and egalitarian welfare (spec §8.6) ---
#
# Hand-computed instance used below. Each valuation sums to 100 points:
#   pool   widgets 10, gadgets 10
#   v_A    widgets 8, gadgets 2   -> 10*8 + 10*2 = 100
#   v_B    widgets 3, gadgets 7   -> 10*3 + 10*7 = 100
#   W* = 10*max(8,3) + 10*max(2,7) = 80 + 70 = 150

import random

import pytest

from src.agents.base import Agent, AgentView
from src.agents.schema import NegotiationAction
from src.environment.allocation import compute_utility
from src.environment.optimum import compute_optimal_welfare
from src.environment.resources import generate_resource_pool
from src.environment.valuations import generate_valuation
from src.evaluation.metrics import egalitarian_welfare, relative_welfare_efficiency
from src.negotiation.protocol import EvaluatorState, NegotiationSession

POOL = {"widgets": 10, "gadgets": 10}
V_A = {"widgets": 8.0, "gadgets": 2.0}
V_B = {"widgets": 3.0, "gadgets": 7.0}


def _agreed(bundle_A, pool=POOL, v_A=V_A, v_B=V_B) -> NegotiationRecord:
    """Agreed record whose utilities and optimum come from the evaluator's
    own ground-truth functions, not from hand-typed numbers."""
    bundle_B = {c: pool[c] - bundle_A[c] for c in pool}
    u_A, u_B = compute_utility(bundle_A, v_A), compute_utility(bundle_B, v_B)
    return _make_record(
        outcome="agreed", final_allocation={"A": bundle_A, "B": bundle_B},
        utility_A=u_A, utility_B=u_B, total_welfare=u_A + u_B,
        optimal_welfare=compute_optimal_welfare(pool, v_A, v_B)[1],
        resource_pool=pool, valuation_A=v_A, valuation_B=v_B,
    )


def _failed(outcome, **extra) -> NegotiationRecord:
    return _make_record(outcome=outcome, final_allocation=None, utility_A=0.0,
                        utility_B=0.0, total_welfare=0.0, optimal_welfare=150.0, **extra)


def test_optimal_welfare_hand_example():
    alloc, w = compute_optimal_welfare(POOL, V_A, V_B)
    assert w == 150.0
    assert alloc.a_A == {"widgets": 10, "gadgets": 0}
    assert alloc.a_B == {"widgets": 0, "gadgets": 10}


def test_rwe_and_ew_agreed_hand_example():
    # A: 8w+2g -> 64+4 = 68.  B: 2w+8g -> 6+56 = 62.  SW = 130.
    r = _agreed({"widgets": 8, "gadgets": 2})
    assert (r.utility_A, r.utility_B) == (68.0, 62.0)
    assert relative_welfare_efficiency(r) == pytest.approx(130 / 150)
    assert egalitarian_welfare(r) == pytest.approx(0.62)


def test_rwe_equals_one_at_optimal_allocation():
    # A: 10w -> 80.  B: 10g -> 70.  SW = 150 = W*.
    r = _agreed({"widgets": 10, "gadgets": 0})
    assert relative_welfare_efficiency(r) == pytest.approx(1.0)
    assert egalitarian_welfare(r) == pytest.approx(0.70)


def test_rwe_and_ew_worst_swap_allocation():
    # A: 10g -> 20.  B: 10w -> 30.  SW = 50.
    r = _agreed({"widgets": 0, "gadgets": 10})
    assert relative_welfare_efficiency(r) == pytest.approx(50 / 150)
    assert egalitarian_welfare(r) == pytest.approx(0.20)


def test_ew_zero_when_one_agent_gets_nothing_but_rwe_positive():
    # A takes everything: u_A = 100, u_B = 0.  SW = 100.
    r = _agreed({"widgets": 10, "gadgets": 10})
    assert egalitarian_welfare(r) == 0.0
    assert relative_welfare_efficiency(r) == pytest.approx(100 / 150)


@pytest.mark.parametrize("outcome,reason", [
    ("walked_away", None),
    ("timeout", None),
    ("invalid_action", "malformed_output"),
    ("invalid_action", "invalid_allocation"),
    ("invalid_action", "illegal_accept"),
])
def test_failures_score_zero_on_unconditional_metrics(outcome, reason):
    r = _failed(outcome, invalid_reason=reason)
    assert relative_welfare_efficiency(r) == 0.0
    assert egalitarian_welfare(r) == 0.0
    # Allocation-dependent metrics stay undefined, not 0.
    assert social_welfare_efficiency(r) is None
    assert equitability(r) is None


def test_failure_scores_zero_even_if_stale_utilities_are_present():
    # Only agreed negotiations with a valid allocation earn value.
    r = _make_record(outcome="timeout", final_allocation=None,
                     utility_A=50.0, utility_B=50.0, total_welfare=100.0, optimal_welfare=150.0)
    assert relative_welfare_efficiency(r) == 0.0
    assert egalitarian_welfare(r) == 0.0


def test_agreed_without_allocation_scores_zero():
    r = _make_record(outcome="agreed", final_allocation=None)
    assert relative_welfare_efficiency(r) == 0.0
    assert egalitarian_welfare(r) == 0.0


def test_rwe_degenerate_zero_optimum_is_zero():
    r = _make_record(total_welfare=0.0, optimal_welfare=0.0)
    assert relative_welfare_efficiency(r) == 0.0


def test_rwe_and_ew_bounds_over_generated_instances():
    """0 <= RWE <= 1 and 0 <= EW <= 1 for every feasible allocation, on
    real generated instances (K = 3). Also W* >= 100 (spec §8.6)."""
    rng = random.Random(0)
    for seed in range(200):
        pool = generate_resource_pool(seed=seed)
        v_A, v_B = generate_valuation(seed, "A", pool), generate_valuation(seed, "B", pool)
        assert compute_optimal_welfare(pool, v_A, v_B)[1] >= 100 - 1e-9
        for _ in range(10):
            r = _agreed({c: rng.randint(0, q) for c, q in pool.items()}, pool, v_A, v_B)
            assert -1e-9 <= relative_welfare_efficiency(r) <= 1 + 1e-9
            assert 0 <= egalitarian_welfare(r) <= 1 + 1e-9
        # The optimal allocation reaches RWE = 1.
        best = compute_optimal_welfare(pool, v_A, v_B)[0].a_A
        assert relative_welfare_efficiency(_agreed(best, pool, v_A, v_B)) == pytest.approx(1.0)


def test_summarize_batch_unconditional_metrics_include_failures():
    records = [
        _agreed({"widgets": 8, "gadgets": 2}),   # RWE 130/150, EW 0.62
        _agreed({"widgets": 10, "gadgets": 0}),  # RWE 1,       EW 0.70
        _failed("walked_away"),
        _failed("timeout"),
        _failed("invalid_action", invalid_reason="illegal_accept"),
    ]
    m = summarize_batch(records)
    assert m.agreement_rate == pytest.approx(2 / 5)
    # Unconditional: failures count as 0, divided by all 5.
    assert m.mean_rwe == pytest.approx((130 / 150 + 1.0) / 5)
    assert m.mean_egalitarian_welfare == pytest.approx((0.62 + 0.70) / 5)
    # Conditional SWE still uses only the 2 agreed negotiations.
    assert m.mean_swe == pytest.approx((130 / 150 + 1.0) / 2)


def test_summarize_batch_all_failures_gives_zero_unconditional():
    m = summarize_batch([_failed("walked_away"), _failed("timeout")])
    assert (m.agreement_rate, m.mean_rwe, m.mean_egalitarian_welfare) == (0.0, 0.0, 0.0)
    assert m.mean_swe is None


class _ScriptedAgent(Agent):
    """A offers 8w+2g to itself while claiming a perfect score in its
    message; B accepts. The metrics must ignore the self-report."""

    def __init__(self, name):
        self.name = name

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        if view.role == "A":
            return NegotiationAction(
                action_type="OFFER",
                allocation={"A": {"widgets": 8, "gadgets": 2}, "B": {"widgets": 2, "gadgets": 8}},
                message="This deal is 100% efficient and perfectly fair.",
            )
        return NegotiationAction(action_type="ACCEPT", message="I got 100 points.")


@pytest.mark.asyncio
async def test_rwe_and_ew_computed_from_evaluator_ground_truth_end_to_end():
    alloc, w = compute_optimal_welfare(POOL, V_A, V_B)
    state = EvaluatorState(
        instance_seed=0, resource_pool=POOL, valuation_A=V_A, valuation_B=V_B,
        optimal_allocation=alloc, optimal_welfare=w, max_rounds=4, first_mover="A",
        method="test", model_A_name="a", model_B_name="b",
    )
    r = await NegotiationSession(_ScriptedAgent("a"), _ScriptedAgent("b"), state).run()
    assert r.outcome == "agreed"
    assert (r.utility_A, r.utility_B, r.optimal_welfare) == (68.0, 62.0, 150.0)
    assert relative_welfare_efficiency(r) == pytest.approx(130 / 150)
    assert egalitarian_welfare(r) == pytest.approx(0.62)


# --- Diagnostics: model imbalance and first-mover gap (spec §8.6) ---

from src.evaluation.metrics import first_mover_gap, model_imbalance

CLAUDE, GPT = "claude:test-model", "openai:test-model"


@pytest.mark.parametrize("model_A,model_B,expected", [
    (CLAUDE, GPT, 0.70 - 0.40),  # Claude in seat A: u_Claude = u_A = 70
    (GPT, CLAUDE, 0.40 - 0.70),  # Claude in seat B: u_Claude = u_B = 40
])
def test_model_imbalance_follows_model_not_seat(model_A, model_B, expected):
    r = _make_record(model_A=model_A, model_B=model_B, utility_A=70.0, utility_B=40.0)
    assert model_imbalance(r, CLAUDE, GPT) == pytest.approx(expected)
    # Reversing the argument order flips the sign.
    assert model_imbalance(r, GPT, CLAUDE) == pytest.approx(-expected)


@pytest.mark.parametrize("first_mover,expected", [
    ("A", 0.70 - 0.40),  # A moved first: u_first = u_A = 70
    ("B", 0.40 - 0.70),  # B moved first: u_first = u_B = 40
])
def test_first_mover_gap_follows_first_mover_field(first_mover, expected):
    r = _make_record(first_mover=first_mover, utility_A=70.0, utility_B=40.0)
    assert first_mover_gap(r) == pytest.approx(expected)


@pytest.mark.parametrize("outcome", ["walked_away", "timeout", "invalid_action"])
def test_diagnostics_undefined_without_agreement(outcome):
    r = _failed(outcome, model_A=CLAUDE, model_B=GPT)
    assert model_imbalance(r, CLAUDE, GPT) is None
    assert first_mover_gap(r) is None


def test_diagnostics_undefined_when_agreed_but_allocation_missing():
    r = _make_record(outcome="agreed", final_allocation=None, model_A=CLAUDE, model_B=GPT)
    assert model_imbalance(r, CLAUDE, GPT) is None
    assert first_mover_gap(r) is None


def test_diagnostics_zero_when_utilities_equal():
    r = _make_record(model_A=CLAUDE, model_B=GPT, utility_A=50.0, utility_B=50.0)
    assert model_imbalance(r, CLAUDE, GPT) == 0.0
    assert first_mover_gap(r) == 0.0


@pytest.mark.parametrize("model_A,model_B,x,y", [
    (CLAUDE, GPT, CLAUDE, "openai:other-model"),  # named model not in either seat
    (CLAUDE, CLAUDE, CLAUDE, GPT),                # same model in both seats: ambiguous
])
def test_model_imbalance_rejects_models_not_matching_seats(model_A, model_B, x, y):
    r = _make_record(model_A=model_A, model_B=model_B)
    with pytest.raises(ValueError):
        model_imbalance(r, x, y)


def test_first_mover_gap_rejects_unknown_first_mover():
    with pytest.raises(ValueError):
        first_mover_gap(_make_record(first_mover="C"))


class _FirstMoverTakesMore(Agent):
    """Whoever moves first offers itself 8 widgets + 2 gadgets; the
    responder accepts. Used to drive the real engine through every
    seat x first-mover cell of the design."""

    def __init__(self, name):
        self.name = name

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        if not view.transcript_so_far:
            other = "B" if view.role == "A" else "A"
            return NegotiationAction(action_type="OFFER", allocation={
                view.role: {"widgets": 8, "gadgets": 2}, other: {"widgets": 2, "gadgets": 8}})
        return NegotiationAction(action_type="ACCEPT")


# Hand-computed with v_A = (8, 2), v_B = (3, 7):
#   A first: A gets 8w+2g = 68, B gets 2w+8g = 62
#   B first: B gets 8w+2g = 38, A gets 2w+8g = 32
@pytest.mark.asyncio
@pytest.mark.parametrize("claude_seat,first_mover,u_A,u_B,imbalance,gap", [
    ("A", "A", 68.0, 62.0, +0.06, +0.06),  # Claude A, Claude first
    ("B", "A", 68.0, 62.0, -0.06, +0.06),  # Claude B, GPT first
    ("A", "B", 32.0, 38.0, -0.06, +0.06),  # Claude A, GPT first
    ("B", "B", 32.0, 38.0, +0.06, +0.06),  # Claude B, Claude first
])
async def test_seat_swap_design_gives_model_and_first_mover_values(
        claude_seat, first_mover, u_A, u_B, imbalance, gap):
    claude, gpt = _FirstMoverTakesMore(CLAUDE), _FirstMoverTakesMore(GPT)
    agent_A, agent_B = (claude, gpt) if claude_seat == "A" else (gpt, claude)
    alloc, w = compute_optimal_welfare(POOL, V_A, V_B)
    state = EvaluatorState(
        instance_seed=0, resource_pool=POOL, valuation_A=V_A, valuation_B=V_B,
        optimal_allocation=alloc, optimal_welfare=w, max_rounds=4, first_mover=first_mover,
        method="test", model_A_name=agent_A.name, model_B_name=agent_B.name,
    )
    r = await NegotiationSession(agent_A, agent_B, state).run()
    assert r.outcome == "agreed" and r.first_mover == first_mover
    assert (r.utility_A, r.utility_B) == (u_A, u_B)
    assert model_imbalance(r, CLAUDE, GPT) == pytest.approx(imbalance)
    assert first_mover_gap(r) == pytest.approx(gap)
