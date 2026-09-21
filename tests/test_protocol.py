import pytest

from src.agents.base import Agent, AgentView
from src.agents.mock_agent import MockAgent
from src.agents.schema import NegotiationAction
from src.environment.optimum import compute_optimal_welfare
from src.environment.resources import generate_resource_pool
from src.environment.valuations import generate_valuation
from src.negotiation.protocol import EvaluatorState, NegotiationSession


def _build_state(seed=1, max_rounds=10, method="test") -> EvaluatorState:
    pool = generate_resource_pool(seed=seed)
    v_a = generate_valuation(seed, "A", pool)
    v_b = generate_valuation(seed, "B", pool)
    _, optimal_welfare = compute_optimal_welfare(pool, v_a, v_b)
    return EvaluatorState(
        instance_seed=seed,
        resource_pool=pool,
        valuation_A=v_a,
        valuation_B=v_b,
        optimal_allocation=compute_optimal_welfare(pool, v_a, v_b)[0],
        optimal_welfare=optimal_welfare,
        max_rounds=max_rounds,
        first_mover="A",
        method=method,
        model_A_name="agent-A",
        model_B_name="agent-B",
    )


class AlwaysWalkAwayAgent(Agent):
    name = "always-walk-away"

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        return NegotiationAction(action_type="WALK_AWAY", message="Nope.")


class MalformedOfferAgent(Agent):
    """Offers more units than exist in a category — protocol must reject
    this safely rather than crash or let it through to scoring."""
    name = "malformed-offer"

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        bad = {cat: qty + 999 for cat, qty in view.resource_pool.items()}
        other_role = "B" if view.role == "A" else "A"
        other = {cat: 0 for cat in view.resource_pool}
        return NegotiationAction(
            action_type="OFFER",
            allocation={view.role: bad, other_role: other},
        )


@pytest.mark.asyncio
async def test_negotiation_converges_to_agreement():
    # Threshold low enough relative to opening/concession that these two
    # mock agents should reach agreement well within the round limit.
    agent_A = MockAgent(name="mock-A", opening_ask=0.6, min_ask=0.3, concession_step=0.1, accept_threshold=0.35)
    agent_B = MockAgent(name="mock-B", opening_ask=0.6, min_ask=0.3, concession_step=0.1, accept_threshold=0.35)
    state = _build_state(seed=3, max_rounds=10)
    record = await NegotiationSession(agent_A, agent_B, state).run()

    assert record.outcome == "agreed"
    assert record.num_rounds <= state.max_rounds
    assert record.total_welfare > 0


@pytest.mark.asyncio
async def test_negotiation_walks_away_immediately():
    agent_A = AlwaysWalkAwayAgent()
    agent_B = MockAgent()
    state = _build_state(seed=4, max_rounds=10)
    record = await NegotiationSession(agent_A, agent_B, state).run()

    assert record.outcome == "walked_away"
    assert record.utility_A == 0.0
    assert record.utility_B == 0.0
    assert record.num_rounds == 1


@pytest.mark.asyncio
async def test_negotiation_times_out_when_no_convergence():
    # Both agents keep countering; the final-turn OFFER hits the deadline.
    agent_A = MockAgent(name="stubborn-A", opening_ask=0.95, min_ask=0.9, concession_step=0.001, accept_threshold=0.9)
    agent_B = MockAgent(name="stubborn-B", opening_ask=0.95, min_ask=0.9, concession_step=0.001, accept_threshold=0.9)
    state = _build_state(seed=5, max_rounds=4)
    record = await NegotiationSession(agent_A, agent_B, state).run()

    assert record.outcome == "timeout"
    assert record.invalid_reason is None
    assert record.final_allocation is None
    assert record.num_rounds == 4
    assert record.utility_A == 0.0
    assert record.utility_B == 0.0


class ScriptedAgent(Agent):
    """Plays a fixed list of actions, one per turn."""

    def __init__(self, *actions: NegotiationAction):
        self.name = "scripted"
        self._actions = list(actions)

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        return self._actions.pop(0)


def _offer(state: EvaluatorState, frac_A: float) -> NegotiationAction:
    a = {c: round(q * frac_A) for c, q in state.resource_pool.items()}
    b = {c: q - a[c] for c, q in state.resource_pool.items()}
    return NegotiationAction(action_type="OFFER", allocation={"A": a, "B": b})


ACCEPT = NegotiationAction(action_type="ACCEPT")
WALK = NegotiationAction(action_type="WALK_AWAY")


@pytest.mark.asyncio
async def test_offer_on_non_final_turn_lets_opponent_respond():
    state = _build_state(seed=20, max_rounds=3)
    offer = _offer(state, 0.6)
    record = await NegotiationSession(ScriptedAgent(offer), ScriptedAgent(ACCEPT), state).run()

    assert record.outcome == "agreed"
    assert record.num_rounds == 2  # B got a turn to respond, with a turn to spare
    assert record.final_allocation == offer.allocation


@pytest.mark.asyncio
async def test_final_turn_accept_reaches_agreement():
    state = _build_state(seed=21, max_rounds=2)
    offer = _offer(state, 0.6)
    record = await NegotiationSession(ScriptedAgent(offer), ScriptedAgent(ACCEPT), state).run()

    assert record.outcome == "agreed"
    assert record.num_rounds == 2
    assert record.invalid_reason is None


@pytest.mark.asyncio
async def test_final_turn_walk_away():
    state = _build_state(seed=22, max_rounds=2)
    record = await NegotiationSession(
        ScriptedAgent(_offer(state, 0.6)), ScriptedAgent(WALK), state
    ).run()

    assert record.outcome == "walked_away"
    assert record.num_rounds == 2
    assert record.final_allocation is None


@pytest.mark.asyncio
async def test_final_turn_offer_is_timeout():
    state = _build_state(seed=23, max_rounds=2)
    record = await NegotiationSession(
        ScriptedAgent(_offer(state, 0.6)), ScriptedAgent(_offer(state, 0.4)), state
    ).run()

    assert record.outcome == "timeout"  # NOT invalid_action
    assert record.invalid_reason is None
    assert record.final_allocation is None
    assert record.num_rounds == state.max_rounds
    assert (record.utility_A, record.utility_B, record.total_welfare) == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_final_turn_invalid_allocation_is_still_invalid_action():
    state = _build_state(seed=27, max_rounds=2)
    bad = NegotiationAction(action_type="OFFER")  # no allocation
    record = await NegotiationSession(ScriptedAgent(_offer(state, 0.6)), ScriptedAgent(bad), state).run()

    assert (record.outcome, record.invalid_reason) == ("invalid_action", "invalid_allocation")


@pytest.mark.asyncio
async def test_accept_without_standing_offer_is_invalid_action():
    state = _build_state(seed=24, max_rounds=5)
    record = await NegotiationSession(ScriptedAgent(ACCEPT), ScriptedAgent(WALK), state).run()

    assert record.outcome == "invalid_action"
    assert record.invalid_reason == "illegal_accept"


@pytest.mark.asyncio
async def test_accept_takes_opponents_offer_never_own():
    # A offers X, B counters Y, A accepts: the standing offer is Y (B's).
    # Literally accepting one's own offer cannot arise in an alternating
    # loop (every non-terminal turn is an OFFER, so the standing offer is
    # always the opponent's); the `last_offer_actor == actor` guard in the
    # protocol is defensive. This pins the observable behavior instead.
    state = _build_state(seed=25, max_rounds=5)
    x, y = _offer(state, 0.7), _offer(state, 0.3)
    record = await NegotiationSession(ScriptedAgent(x, ACCEPT), ScriptedAgent(y), state).run()

    assert record.outcome == "agreed"
    assert record.final_allocation == y.allocation
    assert record.final_allocation != x.allocation


@pytest.mark.asyncio
async def test_invalid_reasons_malformed_and_allocation():
    from src.agents.schema import InvalidAgentOutputError

    class Malformed(Agent):
        name = "malformed"

        async def generate_response(self, view):
            raise InvalidAgentOutputError("not json")

    state = _build_state(seed=26, max_rounds=5)
    rec = await NegotiationSession(Malformed(), MockAgent(), state).run()
    assert (rec.outcome, rec.invalid_reason) == ("invalid_action", "malformed_output")

    no_alloc = NegotiationAction(action_type="OFFER")
    rec = await NegotiationSession(ScriptedAgent(no_alloc), MockAgent(), state).run()
    assert (rec.outcome, rec.invalid_reason) == ("invalid_action", "invalid_allocation")

    rec = await NegotiationSession(MalformedOfferAgent(), MockAgent(), state).run()
    assert rec.invalid_reason == "invalid_allocation"


@pytest.mark.asyncio
async def test_negotiation_always_terminates_within_max_rounds():
    for seed in range(10, 15):
        state = _build_state(seed=seed, max_rounds=6)
        record = await NegotiationSession(MockAgent(), MockAgent(), state).run()
        assert record.num_rounds <= 6


@pytest.mark.asyncio
async def test_malformed_offer_handled_safely_not_crash():
    agent_A = MalformedOfferAgent()
    agent_B = MockAgent()
    state = _build_state(seed=6, max_rounds=10)
    record = await NegotiationSession(agent_A, agent_B, state).run()  # must not raise

    assert record.outcome == "invalid_action"
    assert record.utility_A == 0.0
    assert record.utility_B == 0.0


@pytest.mark.asyncio
async def test_cannot_accept_on_first_turn():
    class ImmediateAcceptAgent(Agent):
        name = "immediate-accept"

        async def generate_response(self, view: AgentView) -> NegotiationAction:
            return NegotiationAction(action_type="ACCEPT")

    state = _build_state(seed=7, max_rounds=10)
    record = await NegotiationSession(ImmediateAcceptAgent(), MockAgent(), state).run()

    assert record.outcome == "invalid_action"
    assert record.invalid_reason == "illegal_accept"
