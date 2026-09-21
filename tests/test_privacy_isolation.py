"""This is the single most important test file per the project brief:
'an agent cannot access the opponent's private valuation through the
environment API'. See docs/spec.md §3.4, §6.
"""
import dataclasses

from src.agents.base import AgentView
from src.agents.mock_agent import MockAgent
from src.environment.optimum import compute_optimal_welfare
from src.environment.resources import generate_resource_pool
from src.environment.valuations import generate_valuation
from src.negotiation.protocol import EvaluatorState, NegotiationSession


def _build_state(seed=1) -> EvaluatorState:
    pool = generate_resource_pool(seed=seed)
    v_a = generate_valuation(seed, "A", pool)
    v_b = generate_valuation(seed, "B", pool)
    alloc, welfare = compute_optimal_welfare(pool, v_a, v_b)
    return EvaluatorState(
        instance_seed=seed, resource_pool=pool, valuation_A=v_a, valuation_B=v_b,
        optimal_allocation=alloc, optimal_welfare=welfare, max_rounds=10,
        first_mover="A", method="test", model_A_name="A", model_B_name="B",
    )


def test_agentview_has_no_opponent_valuation_field_at_all():
    """Structural guarantee: the AgentView type itself has no field that
    could hold the opponent's valuation, the optimal allocation, or the
    optimal welfare. This is stronger than checking a specific instance —
    it holds for every AgentView ever constructed."""
    field_names = {f.name for f in dataclasses.fields(AgentView)}
    forbidden_substrings = ["opponent", "other_valuation", "optimal", "welfare", "evaluator"]
    for name in field_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), (
                f"AgentView field '{name}' looks like it could leak evaluator-only data"
            )
    assert field_names == {
        "role", "resource_pool", "own_valuation", "transcript_so_far",
        "round_number", "rounds_remaining", "max_rounds",
    }


def test_view_for_role_a_only_contains_valuation_a():
    state = _build_state(seed=2)
    session = NegotiationSession(MockAgent(), MockAgent(), state)
    view_a = session._build_view("A", transcript=[], round_number=1)

    assert view_a.own_valuation == state.valuation_A
    # Direct value-level check: B's valuation values must not appear as
    # the own_valuation shown to A (guards against a copy-paste bug that
    # would assign the wrong valuation to the wrong role).
    assert view_a.own_valuation != state.valuation_B


def test_view_for_role_b_only_contains_valuation_b():
    state = _build_state(seed=2)
    session = NegotiationSession(MockAgent(), MockAgent(), state)
    view_b = session._build_view("B", transcript=[], round_number=1)

    assert view_b.own_valuation == state.valuation_B
    assert view_b.own_valuation != state.valuation_A


def test_views_are_independent_objects_not_shared_references():
    """Mutating one agent's view must not be able to affect the
    evaluator's stored state or the other agent's view."""
    state = _build_state(seed=2)
    session = NegotiationSession(MockAgent(), MockAgent(), state)
    view_a = session._build_view("A", transcript=[], round_number=1)

    view_a.resource_pool["widgets"] = -9999  # tamper with A's copy
    assert state.resource_pool.get("widgets", 0) != -9999  # evaluator state untouched

    view_b = session._build_view("B", transcript=[], round_number=1)
    assert view_b.resource_pool.get("widgets", 0) != -9999  # B's fresh view is unaffected too
