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
    # anthropic SDK 1.x has no typed `temperature` argument; it goes in extra_body.
    temperature = sent["extra_body"]["temperature"] if kind == "claude" else sent["temperature"]
    assert temperature == 0.3
    assert sent["max_tokens" if kind == "claude" else "max_completion_tokens"] == 321


# ------------------------------------------- frozen runtime settings (§8.15)
EFFORT_CFG = GenerationConfig(model="stub-model", temperature=1.0, max_output_tokens=1024,
                              timeout_s=120, max_retries=3, retry_backoff_s=0, effort="medium")


@pytest.mark.asyncio
async def test_claude_sends_thinking_disabled_effort_and_no_seed():
    client = StubClient(claude_resp(tool_input={"action_type": "WALK_AWAY"}))
    await run_first_turn(ClaudeAgent(EFFORT_CFG, client=client))
    sent = client.calls[0]
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["output_config"] == {"effort": "medium"}
    assert sent["extra_body"] == {"temperature": 1.0}
    assert "seed" not in sent and "temperature" not in sent


@pytest.mark.asyncio
async def test_claude_sends_no_effort_when_unset():
    client = StubClient(claude_resp(tool_input={"action_type": "WALK_AWAY"}))
    await run_first_turn(ClaudeAgent(CFG, client=client))
    assert "output_config" not in client.calls[0]


@pytest.mark.asyncio
async def test_openai_sends_no_reasoning_effort_and_no_seed():
    client = StubClient(openai_resp(content=json.dumps({"action_type": "WALK_AWAY"})))
    await run_first_turn(OpenAIAgent(CFG, client=client))
    sent = client.calls[0]
    assert "reasoning_effort" not in sent and "seed" not in sent


def test_openai_agent_rejects_effort():
    with pytest.raises(ConfigError, match="effort"):
        OpenAIAgent(EFFORT_CFG, client=StubClient())


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_sent_kwargs_match_installed_sdk_signature(kind):
    """The stub accepts any kwargs, so bind what each agent sends against the
    real installed SDK method (no network): an argument the pinned SDK does
    not accept would otherwise only fail on the first real API call."""
    import inspect
    walk = {"action_type": "WALK_AWAY"}
    if kind == "claude":
        from anthropic.resources.messages import AsyncMessages as Resource
        client = StubClient(claude_resp(tool_input=walk))
        agent = ClaudeAgent(EFFORT_CFG, client=client)
    else:
        from openai.resources.chat.completions import AsyncCompletions as Resource
        client = StubClient(openai_resp(content=json.dumps(walk)))
        agent = OpenAIAgent(CFG, client=client)
    await run_first_turn(agent)
    inspect.signature(Resource.create).bind(None, **client.calls[0])  # TypeError if not accepted


@pytest.mark.asyncio
async def test_claude_temperature_serialized_by_installed_sdk():
    """Real anthropic client over an in-process mock transport (no network):
    the SDK itself builds and serializes the request, and the JSON body it
    would send carries temperature = 1.0 as a top-level field."""
    import anthropic
    import httpx2

    sent = []

    def handler(request):
        sent.append(request)
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-4-6",
            "content": [{"type": "tool_use", "id": "toolu_test", "name": "submit_negotiation_action",
                         "input": {"action_type": "WALK_AWAY"}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    client = anthropic.AsyncAnthropic(
        api_key="test-key-not-used", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    record, _ = await run_first_turn(ClaudeAgent(EFFORT_CFG, client=client))

    assert record.outcome == "walked_away"
    (request,) = sent
    assert request.method == "POST" and request.url.path == "/v1/messages"
    body = json.loads(request.content)
    assert body["temperature"] == 1.0          # top level of the Messages request body
    assert "extra_body" not in body            # merged by the SDK, not nested


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
    assert cfg.effort is None  # optional; unset = not sent
    assert GenerationConfig.from_env("X", {**env, "X_EFFORT": "medium"}).effort == "medium"
    with pytest.raises(ConfigError, match="effort"):
        GenerationConfig.from_env("X", {**env, "X_EFFORT": "extreme"})


def test_env_example_matches_approved_runtime():
    """.env.example documents exactly the frozen values (spec §8.15)."""
    from pathlib import Path
    from dotenv import dotenv_values
    from src.experiments.config import APPROVED_OPERATIONAL, APPROVED_SCIENTIFIC

    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example")
    for prefix, kind in (("CLAUDE", "ClaudeAgent"), ("OPENAI", "OpenAIAgent")):
        cfg = GenerationConfig.from_env(prefix, env)
        for field, want in {**APPROVED_SCIENTIFIC[kind], **APPROVED_OPERATIONAL}.items():
            assert getattr(cfg, field) == want, (prefix, field)


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


# ------------------------------------------- structured-output schemas
def _strict_violations(node, path="$"):
    """OpenAI strict structured-output rules for objects: every object lists
    its properties, forbids additional ones, and requires all of them."""
    out = []
    if not isinstance(node, dict):
        return out
    types = node.get("type")
    if types == "object" or (isinstance(types, list) and "object" in types):
        props = node.get("properties")
        if not isinstance(props, dict):
            out.append(f"{path}: object without properties")
            props = {}
        if node.get("additionalProperties") is not False:
            out.append(f"{path}: additionalProperties is not false")
        if set(node.get("required", [])) != set(props):
            out.append(f"{path}: not every property is required")
    for key, child in (node.get("properties") or {}).items():
        out += _strict_violations(child, f"{path}.{key}")
    for i, child in enumerate(node.get("anyOf", [])):
        out += _strict_violations(child, f"{path}.anyOf[{i}]")
    return out


def _allocation_object(schema: dict) -> dict:
    """The non-null branch of the allocation property."""
    branches = schema["properties"]["allocation"]["anyOf"]
    assert {"type": "null"} in branches
    return next(b for b in branches if b.get("type") == "object")


def _sent_schema(kind, client) -> dict:
    sent = client.calls[0]
    if kind == "claude":
        return sent["tools"][0]["input_schema"]
    return sent["response_format"]["json_schema"]["schema"]


def test_strict_checker_flags_the_pre_fix_openai_schema():
    pre_fix = {
        "type": "object",
        "properties": {
            "action_type": {"type": "string"},
            "allocation": {"type": ["object", "null"], "description": "free-form map"},
            "message": {"type": ["string", "null"]},
        },
        "required": ["action_type", "allocation", "message"],
        "additionalProperties": False,
    }
    assert _strict_violations(pre_fix) == [
        "$.allocation: object without properties",
        "$.allocation: additionalProperties is not false",
    ]


@pytest.mark.asyncio
async def test_openai_schema_sent_is_strict_valid_and_keyed_by_pool():
    agent, client = make_agent("openai", wrap("openai", {"action_type": "WALK_AWAY",
                                                         "allocation": None, "message": None}))
    _, state = await run_first_turn(agent)

    json_schema = client.calls[0]["response_format"]["json_schema"]
    assert json_schema["strict"] is True
    assert "anyOf" not in json_schema["schema"]  # strict root must be a plain object
    assert _strict_violations(json_schema["schema"]) == []
    alloc = _allocation_object(json_schema["schema"])
    assert list(alloc["properties"]) == list(state.resource_pool)
    assert all(p == {"type": "integer"} for p in alloc["properties"].values())


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_schema_conforming_offer_passes_parser_and_protocol_unchanged(kind):
    """Both providers get the same allocation shape, and an output built from
    exactly the schema's keys goes through the existing parser + validator."""
    probe, probe_client = make_agent(kind, wrap(kind, {"action_type": "WALK_AWAY"}))
    _, state = await run_first_turn(probe)
    alloc = _allocation_object(_sent_schema(kind, probe_client))
    assert alloc["required"] == list(state.resource_pool)
    assert alloc["additionalProperties"] is False

    mine = {c: state.resource_pool[c] // 3 for c in alloc["properties"]}
    agent, _ = make_agent(kind, wrap(kind, {"action_type": "OFFER", "allocation": mine,
                                            "message": None}))
    record, _ = await run_first_turn(agent)
    assert record.outcome == "walked_away"  # A's offer was accepted as valid; B walked
    offer = record.transcript[0].action.allocation
    assert offer["A"] == mine
    assert offer["B"] == {c: q - mine[c] for c, q in state.resource_pool.items()}


def test_provider_schemas_identical_allocation_shape_and_fingerprinted_constants():
    from src.agents.claude_agent import ACTION_TOOL, action_tool
    from src.agents.openai_agent import RESPONSE_SCHEMA, response_schema
    from src.environment.resources import generate_resource_pool

    pool = generate_resource_pool(seed=31)
    claude_alloc = _allocation_object(action_tool(pool)["input_schema"])
    openai_alloc = _allocation_object(response_schema(pool)["schema"])
    # Same keys/types/requiredness for both providers; only descriptions differ.
    strip = lambda s: {k: v for k, v in s.items() if k != "description"}
    assert strip(claude_alloc) == strip(openai_alloc)
    # provenance.prompt_hash fingerprints these constants: they must equal
    # what is actually sent for a default-generated pool.
    assert ACTION_TOOL == action_tool(pool)
    assert RESPONSE_SCHEMA == response_schema(pool)
