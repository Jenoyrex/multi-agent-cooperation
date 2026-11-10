"""The sandbox_v1 protocol: event timing, offer invalidation, W* before and after, determinism, sweeps."""
import asyncio
import subprocess
from pathlib import Path

import pytest

from sandbox.engine import EventSession, SandboxView, event_values
from src.agents.base import Agent
from src.agents.mock_agent import MockAgent
from src.agents.schema import NegotiationAction
from src.environment.allocation import Allocation
from src.environment.optimum import compute_optimal_welfare
from src.negotiation.protocol import EvaluatorState, NegotiationSession
from tests.test_sandbox_models import design, isolated, run  # noqa: F401
from workbench import agents as A, api as WA, environments as E, simulations as S
from workbench.agents import PriorityAgent, WorkbenchError

ROOT = Path(__file__).resolve().parents[1]
POOL = {"food": 10, "water": 10}
VAL = {"A": {"food": 8.0, "water": 2.0}, "B": {"food": 2.0, "water": 8.0}}
EVENT = {"turn": 3, "category": "fuel", "units": 4, "max_points": 40, "extend_rounds": 0}


class Script(Agent):
    """Plays the given actions in order, and records what it was shown."""

    def __init__(self, *moves):
        self.name, self.moves, self.views = "script", list(moves), []

    async def generate_response(self, view):
        self.views.append(view)
        m = self.moves.pop(0)
        return m(view) if callable(m) else m


def half(view):
    other = "B" if view.role == "A" else "A"
    mine = {c: q // 2 for c, q in view.resource_pool.items()}
    return NegotiationAction(action_type="OFFER", allocation={view.role: mine, other: {c: q - mine[c] for c, q in view.resource_pool.items()}})


ACCEPT = NegotiationAction(action_type="ACCEPT")
WALK = NegotiationAction(action_type="WALK_AWAY")


def session(a, b, events=(EVENT,), max_rounds=8, seed=1, pool=POOL):
    _, w = compute_optimal_welfare(pool, VAL["A"], VAL["B"])
    return EventSession(a, b, seed=seed, pool=pool, valuation=VAL, optimal_welfare=w, max_rounds=max_rounds, first_mover="A", method="baseline_v1",
                        model_A="a", model_B="b", events=list(events))


def go(s):
    return asyncio.run(s.run())


def test_event_fires_at_the_start_of_its_turn_and_both_agents_are_told():
    a, b = Script(half, half, WALK), Script(half, half)
    s = session(a, b)
    go(s)
    assert "fuel" not in a.views[0].resource_pool and "fuel" not in b.views[0].resource_pool  # turns 1, 2
    assert a.views[1].round_number == 3 and a.views[1].resource_pool["fuel"] == 4  # turn 3
    assert "New resource added" in a.views[1].notices[0] and "4 units of fuel" in a.views[1].notices[0]
    assert b.views[1].notices == a.views[1].notices
    assert isinstance(a.views[1], SandboxView) and a.views[0].notices == ()
    assert s.fired[0]["turn"] == 3


def test_each_agent_gets_only_its_own_private_value_for_the_new_resource():
    a, b = Script(half, half, WALK), Script(half, half)
    s = session(a, b, seed=5)
    go(s)
    values = event_values(5, EVENT)
    assert a.views[1].own_valuation["fuel"] == values["A"] / 4 and b.views[1].own_valuation["fuel"] == values["B"] / 4
    assert not any(str(values["B"] / 4) in n for n in a.views[1].notices)


def test_a_standing_offer_is_void_after_an_event():
    # B offers on turn 2; the event starts turn 3, so A cannot accept that offer any more.
    a, b = Script(half, ACCEPT), Script(half)
    rec = go(session(a, b))
    assert rec.outcome == "invalid_action" and rec.invalid_reason == "illegal_accept" and rec.num_rounds == 3


def test_the_void_offer_leaves_the_views_but_stays_in_the_record():
    a, b = Script(half, half, WALK), Script(half, half)
    rec = go(session(a, b))
    shown = [(t.turn_number, t.actor) for t in a.views[1].transcript_so_far]
    assert shown == [(1, "A")]  # B's turn-2 offer is gone from what agents see
    assert "offer Agent B made in round 2" in a.views[1].notices[0]
    assert [(t.turn_number, t.actor) for t in rec.transcript][:2] == [(1, "A"), (2, "B")]


def test_an_offer_must_cover_the_new_resource():
    old_shape = lambda v: NegotiationAction(action_type="OFFER", allocation={"A": {"food": 5, "water": 5}, "B": {"food": 5, "water": 5}})  # noqa: E731
    rec = go(session(Script(half, old_shape), Script(half)))
    assert rec.outcome == "invalid_action" and rec.invalid_reason == "invalid_allocation"


def test_a_new_offer_after_the_event_can_be_accepted_and_scores_on_the_final_pool():
    a, b = Script(half, half), Script(half, ACCEPT)
    s = session(a, b, seed=2)
    rec = go(s)
    assert rec.outcome == "agreed" and rec.final_allocation["A"]["fuel"] == 2
    pts = event_values(2, EVENT)
    _, final_w = compute_optimal_welfare({**POOL, "fuel": 4}, {**VAL["A"], "fuel": pts["A"] / 4}, {**VAL["B"], "fuel": pts["B"] / 4})
    _, pre_w = compute_optimal_welfare(POOL, VAL["A"], VAL["B"])
    assert rec.optimal_welfare == pytest.approx(final_w) and s.pre_event_welfare == pytest.approx(pre_w) and final_w > pre_w
    assert rec.resource_pool["fuel"] == 4 and rec.valuation_A["fuel"] == pts["A"] / 4
    assert s.fired[0]["optimal_welfare_after"] == pytest.approx(final_w)


def test_a_negotiation_that_ends_first_never_sees_the_event():
    s = session(Script(WALK), Script())
    rec = go(s)
    assert s.fired == [] and rec.resource_pool == POOL and rec.optimal_welfare == s.pre_event_welfare


def test_more_units_of_an_existing_category_keep_their_value():
    a, b = Script(half, half, WALK), Script(half, half)
    s = session(a, b, events=[{**EVENT, "category": "food"}])
    go(s)
    assert a.views[1].resource_pool["food"] == 14 and a.views[1].own_valuation["food"] == 8.0 and s.fired[0]["points"] is None


def test_the_deadline_stays_unless_the_user_extends_it():
    a, b = Script(half, half, WALK), Script(half, half)
    go(session(a, b, max_rounds=8))
    assert a.views[1].max_rounds == 8
    a, b = Script(half, half, WALK), Script(half, half)
    go(session(a, b, events=[{**EVENT, "extend_rounds": 4}], max_rounds=8))
    assert a.views[0].max_rounds == 8 and a.views[1].max_rounds == 12 and a.views[1].rounds_remaining == 9


def test_same_seed_same_result_and_different_seed_can_differ():
    def once(seed):
        return go(session(MockAgent("x"), PriorityAgent("y"), seed=seed))
    r1, r2 = once(3), once(3)
    assert r1.outcome == r2.outcome and r1.valuation_A == r2.valuation_A and r1.valuation_B == r2.valuation_B
    assert [t.action for t in r1.transcript] == [t.action for t in r2.transcript]
    assert len({event_values(s, EVENT)["A"] for s in range(20)}) > 1


@pytest.mark.parametrize("make", [lambda: (MockAgent("x"), MockAgent("y")), lambda: (PriorityAgent("x"), MockAgent("y")),
                                  lambda: (Script(half, WALK), Script(half)), lambda: (Script(ACCEPT), Script()),
                                  lambda: (MockAgent("x", opening_ask=0.9, min_ask=0.9, accept_threshold=0.99), MockAgent("y", opening_ask=0.9, min_ask=0.9, accept_threshold=0.99))])
def test_without_events_this_engine_matches_the_frozen_one(make):
    a1, b1 = make()
    alloc, w = compute_optimal_welfare(POOL, VAL["A"], VAL["B"])
    state = EvaluatorState(instance_seed=1, resource_pool=POOL, valuation_A=VAL["A"], valuation_B=VAL["B"], optimal_allocation=alloc, optimal_welfare=w,
                           max_rounds=8, first_mover="A", method="baseline_v1", model_A_name="a", model_B_name="b")
    frozen = asyncio.run(NegotiationSession(a1, b1, state).run())
    a2, b2 = make()
    mine = go(session(a2, b2, events=()))
    for field in ("outcome", "num_rounds", "final_allocation", "utility_A", "utility_B", "total_welfare", "optimal_welfare", "invalid_reason", "invalid_detail", "max_rounds"):
        assert getattr(mine, field) == getattr(frozen, field), field
    assert [t.action for t in mine.transcript] == [t.action for t in frozen.transcript]


def test_rule_based_agents_complete_an_event_experiment_end_to_end():
    doc = run(design("builtin-priority", "builtin-concession", repetitions=2, swap_seats=True, swap_first_mover=True,
                     events=[{"turn": 2, "category": "fuel", "units": 4}]))
    assert doc["status"] == "completed" and doc["config"]["sandbox_protocol"] == "sandbox_v1"
    assert "EventSession" in doc["provenance"]["engine"]
    fired = [t for t in doc["trials"] if t["events_fired"]]
    assert fired and all(t["final_pool"]["fuel"] == 4 for t in fired) and all(t["status"] == "completed" for t in doc["trials"])
    assert all(t["optimal_welfare"] > t["pre_event_optimal_welfare"] or t["events_fired"][0]["points"] == {"A": 0, "B": 0} for t in fired)


def test_event_validation():
    bad = [{"turn": 1, "category": "fuel", "units": 4}, {"turn": 10, "category": "fuel", "units": 4}, {"turn": 3, "category": "Fuel 9!", "units": 4},
           {"turn": 3, "category": "fuel", "units": 0}, {"turn": 3, "category": "fuel", "units": 4, "max_points": 500},
           {"turn": 3, "category": "fuel", "units": 4, "extend_rounds": 99}]
    for ev in bad:
        with pytest.raises(WorkbenchError):
            S.validate(design("builtin-priority", "builtin-concession", events=[ev]))
    with pytest.raises(WorkbenchError):
        S.validate(design("builtin-priority", "builtin-concession", events=[{"turn": 3, "category": "a", "units": 1}, {"turn": 3, "category": "b", "units": 1}]))


# ---------------------------------------------------------------- sweeps
SWEEP = ["builtin-field-hospital", "builtin-shared-lab"]


def test_sweep_runs_every_resource_set_with_the_same_seeds():
    doc = run(design("builtin-priority", "builtin-concession", environments=SWEEP, repetitions=3))
    assert doc["plan_size"] == len(doc["trials"]) == 6
    assert {t["env_name"] for t in doc["trials"]} == {E.BUILTIN[i]["name"] for i in SWEEP}
    seeds = {i: [t["seed"] for t in doc["trials"] if t["env_index"] == i] for i in (0, 1)}
    assert seeds[0] == seeds[1] == [0, 1, 2]
    assert {t["optimal_welfare"] for t in doc["trials"] if t["env_index"] == 0} != {t["optimal_welfare"] for t in doc["trials"] if t["env_index"] == 1}
    assert doc["config"]["environments"][1]["name"] == E.BUILTIN[SWEEP[1]]["name"]


def test_sweep_respects_the_limit_and_refuses_unknown_sets():
    with pytest.raises(WorkbenchError, match="limit"):
        S.validate(design("builtin-priority", "builtin-concession", environments=SWEEP, repetitions=50, swap_seats=True, swap_first_mover=True,
                          conditions=["baseline_v1", "structured_v1"]))
    with pytest.raises(WorkbenchError):
        S.validate(design("builtin-priority", "builtin-concession", environments=["builtin-field-hospital", "nope"]))
    with pytest.raises(WorkbenchError):
        S.validate(design("builtin-priority", "builtin-concession", environments=[SWEEP[0], SWEEP[0]]))


def test_personas_need_a_prompt_agent():
    with pytest.raises(WorkbenchError, match="prompt agents"):
        S.validate(design("builtin-priority", "builtin-concession", personas=[{"name": "kind", "text": "Be kind."}]))


def test_personas_multiply_the_design_and_reach_the_model():
    from tests.sandbox_fake import FakeModels
    from tests.test_sandbox_models import agent, connection
    fake = FakeModels()
    try:
        doc = run(design(agent(connection(fake)), personas=[{"name": "kind", "text": "PERSONA-KIND"}, {"name": "firm", "text": "PERSONA-FIRM"}]))
        assert [t["persona"] for t in doc["trials"]] == ["kind", "firm"]
        systems = [next(m["content"] for m in r["body"]["messages"] if m["role"] == "system") for r in fake.chats()]
        assert any("PERSONA-KIND" in s for s in systems) and any("PERSONA-FIRM" in s for s in systems)
    finally:
        fake.close()


def test_old_saved_experiments_still_load_and_analyse():
    from workbench.analytics import analyse
    doc = run(design("builtin-priority", "builtin-concession"))
    old_cfg = {k: v for k, v in doc["config"].items() if k not in ("environments", "events", "personas", "prices", "caps", "source_labels", "sandbox_protocol")}
    old_trials = [{k: v for k, v in t.items() if k not in ("usage", "final_pool", "final_valuation", "valuation", "events_fired", "pre_event_optimal_welfare")} for t in doc["trials"]]
    old = {**{k: v for k, v in doc.items() if k != "stop_reason"}, "config": old_cfg, "trials": old_trials}
    assert analyse(old)["n_completed"] == 1
    assert S._environments(old_cfg) == [old_cfg["environment"]]


def test_the_frozen_study_is_untouched():
    out = subprocess.run(["git", "diff", "--stat", "main", "--", "docs/spec.md", "src/"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode:
        pytest.skip("not a git checkout with a main branch")
    assert out.stdout.strip() == ""
