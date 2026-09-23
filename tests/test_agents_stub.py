"""Stub-client tests for both real-model agents. No SDK, network, or API key
is involved: each agent gets a fake client that returns canned responses.

Core guarantee under test: a malformed/invalid model output is never
silently transformed into a different valid strategic action.
"""
import json
from types import SimpleNamespace

import pytest

from src.agents.claude_agent import ClaudeAgent
from src.agents.generation import ConfigError, GenerationConfig
from src.agents.openai_agent import OpenAIAgent
from src.agents.schema import TransportError
from src.negotiation.protocol import NegotiationSession
from tests.test_protocol import AlwaysWalkAwayAgent, ScriptedAgent, _build_state, _offer

CFG = GenerationConfig(model="stub-model", temperature=0.3, max_output_tokens=321,
                       timeout_s=12.5, max_retries=2, retry_backoff_s=0)


# ---------------------------------------------------------------- stubs
class ApiError(Exception):
    def __init__(self, status_code=None):
        super().__init__(f"api error {status_code}")
        self.status_code = status_code


def claude_resp(tool_input=None, text=None, stop_reason="tool_use", usage=(100, 20)):
    content = []
    if tool_input is not None:
        content.append(SimpleNamespace(type="tool_use", name="submit_negotiation_action", input=tool_input))
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=content, stop_reason=stop_reason, model="claude-returned",
                           usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1])
                           if usage else None)


def openai_resp(content=None, refusal=None, finish_reason="stop", usage=(100, 20)):
    msg = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)],
                           model="gpt-returned",
                           usage=SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
                           if usage else None)


class StubClient:
    def __init__(self, *items):
        self.items, self.calls = list(items), []
        create = self._create
        self.messages = SimpleNamespace(create=create)                         # Anthropic shape
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))  # OpenAI shape

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make_agent(kind, *items):
    client = StubClient(*items)
    cls = ClaudeAgent if kind == "claude" else OpenAIAgent
    return cls(CFG, client=client), client


def wrap(kind, raw: dict, **kw):
    """Model output `raw` as this provider's response."""
    if kind == "claude":
        return claude_resp(tool_input=raw, **kw)
    return openai_resp(content=json.dumps(raw), **kw)


async def run_first_turn(agent, max_rounds=3):
    """Agent is A and moves first; B walks away. Returns (record, state)."""
    state = _build_state(seed=31, max_rounds=max_rounds)
    return await NegotiationSession(agent, AlwaysWalkAwayAgent(), state).run(), state


KINDS = ["claude", "openai"]


# ------------------------------------------------- allocation: no repair
def _cases(pool):
    cats = list(pool)
    half = {c: q // 2 for c, q in pool.items()}
    return {
        "missing_category": {c: half[c] for c in cats[1:]},
        "null_allocation": None,
        "out_of_range": {**half, cats[0]: pool[cats[0]] + 5},
        "non_integer": {**half, cats[0]: 3.5},
        "non_integer_string": {**half, cats[0]: "3"},
        "boolean_quantity": {**half, cats[0]: True},
        "negative": {**half, cats[0]: -1},
        "extra_category": {**half, "bonus": 1},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("case", list(_cases({"a": 10}).keys()))
async def test_bad_allocation_is_invalid_action_never_repaired(kind, case):
    state = _build_state(seed=31)
    alloc = _cases(state.resource_pool)[case]
    raw = {"action_type": "OFFER", "allocation": alloc, "message": "hi"}
    agent, _ = make_agent(kind, wrap(kind, raw))

    record, _ = await run_first_turn(agent)

    assert record.outcome == "invalid_action"
    assert record.invalid_reason == "invalid_allocation"
    assert record.invalid_detail
    assert record.final_allocation is None
    assert record.transcript == []  # nothing was turned into a valid offer
    # The model's raw output is preserved verbatim for diagnostics.
    assert json.loads(record.calls[0]["raw_output"]) == raw
    assert record.calls[0]["input_tokens"] == 100 and record.calls[0]["output_tokens"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_valid_allocation_passes_through_unchanged(kind):
    state = _build_state(seed=31)
    mine = {c: q // 2 for c, q in state.resource_pool.items()}
    raw = {"action_type": "OFFER", "allocation": mine, "message": "fair split"}
    agent, client = make_agent(kind, wrap(kind, raw))

    record, _ = await run_first_turn(agent)

    assert record.outcome == "walked_away"  # B's reply; A's offer was valid
    offer = record.transcript[0].action
    assert offer.allocation["A"] == mine
    assert offer.allocation["B"] == {c: q - mine[c] for c, q in state.resource_pool.items()}
    assert offer.message == "fair split"
    call = record.calls[0]
    assert (call["input_tokens"], call["output_tokens"], call["attempts"]) == (100, 20, 1)
    assert call["latency_s"] is not None and call["model"] == "stub-model"
    assert call["response_model"] in ("claude-returned", "gpt-returned")


# -------------------------------------- malformed / refusal / truncated
def _malformed_items(kind):
    if kind == "claude":
        return {
            "not_an_object": claude_resp(tool_input="garbage"),
            "unknown_action_type": claude_resp(tool_input={"action_type": "MAYBE"}),
            "empty_no_tool_block": claude_resp(tool_input=None),
            "refusal_text_only": claude_resp(tool_input=None, text="I can't help with that.",
                                             stop_reason="end_turn"),
            "truncated": claude_resp(tool_input={"action_type": "OFFER"}, stop_reason="max_tokens"),
        }
    return {
        "not_an_object": openai_resp(content="[1, 2]"),
        "unknown_action_type": openai_resp(content='{"action_type": "MAYBE"}'),
        "not_json": openai_resp(content="not json {"),
        "empty_content": openai_resp(content=None),
        "refusal": openai_resp(content=None, refusal="I can't help with that."),
        "truncated": openai_resp(content='{"action_type": "OFF', finish_reason="length"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_malformed_refusal_truncated_are_invalid_action(kind):
    for name, resp in _malformed_items(kind).items():
        agent, _ = make_agent(kind, resp)
        record, _ = await run_first_turn(agent)
        assert record.outcome == "invalid_action", name
        assert record.invalid_reason == "malformed_output", name
        assert record.transcript == [], name
        assert len(record.calls) == 1 and record.calls[0]["input_tokens"] == 100, name
    # Truncated output records its cause.
    agent, _ = make_agent(kind, _malformed_items(kind)["truncated"])
    record, _ = await run_first_turn(agent)
    assert "truncated" in record.invalid_detail


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_missing_allocation_key_on_offer(kind):
    agent, _ = make_agent(kind, wrap(kind, {"action_type": "OFFER"}))
    record, _ = await run_first_turn(agent)
    assert (record.outcome, record.invalid_reason) == ("invalid_action", "invalid_allocation")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_walk_away_and_accept_parse(kind):
    agent, _ = make_agent(kind, wrap(kind, {"action_type": "WALK_AWAY", "message": "bye"}))
    record, _ = await run_first_turn(agent)
    assert record.outcome == "walked_away"
    agent, _ = make_agent(kind, wrap(kind, {"action_type": "ACCEPT"}))
    record, _ = await run_first_turn(agent)
    assert (record.outcome, record.invalid_reason) == ("invalid_action", "illegal_accept")


# ------------------------------------------------ explicit generation cfg
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_generation_settings_are_sent_explicitly(kind):
    agent, client = make_agent(kind, wrap(kind, {"action_type": "WALK_AWAY"}))
    await run_first_turn(agent)
    sent = client.calls[0]
    assert sent["model"] == "stub-model"
    assert sent["temperature"] == 0.3
    assert sent["max_tokens" if kind == "claude" else "max_completion_tokens"] == 321


def test_generation_config_from_env():
    env = {"X_MODEL": "m", "X_TEMPERATURE": "0.7", "X_MAX_OUTPUT_TOKENS": "500",
           "X_TIMEOUT_S": "30", "X_MAX_RETRIES": "1"}
    cfg = GenerationConfig.from_env("X", env)
    assert (cfg.model, cfg.temperature, cfg.max_output_tokens, cfg.timeout_s, cfg.max_retries) == \
        ("m", 0.7, 500, 30.0, 1)
    for missing in list(env):
        with pytest.raises(ConfigError, match=missing):
            GenerationConfig.from_env("X", {k: v for k, v in env.items() if k != missing})
    with pytest.raises(ConfigError):
        GenerationConfig.from_env("X", {**env, "X_TEMPERATURE": "hot"})


# ------------------------------------------------------------ retries
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_retry_then_success_records_attempts(kind):
    ok = wrap(kind, {"action_type": "WALK_AWAY"})
    agent, client = make_agent(kind, ApiError(500), ConnectionError("reset"), ok)
    record, _ = await run_first_turn(agent)
    assert record.outcome == "walked_away"
    assert len(client.calls) == 3
    assert record.calls[0]["attempts"] == 3
    # One logical turn => one strategic action, however many attempts.
    assert len(record.transcript) == 1 and record.api_calls == 1 and record.api_attempts == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_retries_exhausted_is_transport_failure_not_an_outcome(kind):
    from src.negotiation.protocol import NegotiationAborted

    agent, client = make_agent(kind, ApiError(503), ApiError(503), ApiError(503))
    with pytest.raises(NegotiationAborted) as ei:
        await run_first_turn(agent)
    assert ei.value.reason == "transport_failure"
    assert len(client.calls) == 3  # 1 + max_retries(2)
    assert ei.value.calls[0]["attempts"] == 3 and "503" in ei.value.calls[0]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_non_retryable_api_error_fails_immediately(kind):
    agent, client = make_agent(kind, ApiError(401))
    with pytest.raises(TransportError) as ei:
        await agent.generate_response(_view())
    assert ei.value.attempts == 1 and len(client.calls) == 1
    assert agent.last_call.error and agent.last_call.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_programming_errors_are_not_disguised_as_transport(kind):
    agent, _ = make_agent(kind, ValueError("bug"))
    with pytest.raises(ValueError):
        await agent.generate_response(_view())


def _view():
    from src.agents.base import AgentView
    state = _build_state(seed=31)
    return AgentView(role="A", resource_pool=state.resource_pool, own_valuation=state.valuation_A,
                     transcript_so_far=[], round_number=1, rounds_remaining=2, max_rounds=3)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_valid_accept_of_standing_offer_agrees(kind):
    state = _build_state(seed=31, max_rounds=4)  # A (scripted) offers first, real agent is B
    offer = _offer(state, 0.6)
    agent, _ = make_agent(kind, wrap(kind, {"action_type": "ACCEPT", "message": "deal"}))

    record = await NegotiationSession(ScriptedAgent(offer), agent, state).run()

    assert record.outcome == "agreed" and record.invalid_reason is None
    assert record.final_allocation == offer.allocation
    assert record.calls[0]["actor"] == "B" and record.calls[0]["input_tokens"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_missing_usage_metadata_stays_unknown_not_zero(kind):
    agent, _ = make_agent(kind, wrap(kind, {"action_type": "WALK_AWAY"}, usage=None))
    record, _ = await run_first_turn(agent)

    call = record.calls[0]
    assert call["input_tokens"] is None and call["output_tokens"] is None
    assert record.input_tokens is None and record.output_tokens is None and record.cost_usd is None
    assert record.api_calls == 1 and call["latency_s"] is not None
