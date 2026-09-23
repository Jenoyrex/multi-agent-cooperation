"""Instruction-strategy variants (baseline_v1 vs structured_v1). These are
prompt conditions only: the protocol engine, schemas, user-turn prompt and
information given to agents are identical across variants. No API is used."""
import hashlib
import json
import sqlite3

import pytest

from src.agents.base import AgentView
from src.agents.claude_agent import ClaudeAgent
from src.agents.mock_agent import MockAgent
from src.agents.openai_agent import OpenAIAgent
from src.agents.prompting import (
    INSTRUCTION_VARIANTS, STRUCTURED_V1_BLOCK, SYSTEM_INSTRUCTIONS, build_prompt, system_instructions,
)
from src.experiments import provenance
from src.experiments.config import ExperimentConfig
from src.experiments.runner import run_batch
from tests.test_agents_stub import CFG, KINDS, StubClient, make_agent, run_first_turn, wrap
from tests.test_protocol import _build_state

# sha256 of the prompts produced by the frozen code (commit e1f1974) for the
# views below, captured before any variant existed. Baseline must never move.
GOLDEN = {
    "template": "2f8f334305598e0c47bd4d810d5040e82db900917d46af85218d5355de3989cd",
    ("A", "system"): "f50b4b71beaa8c7c6e66f22bdce9de495794bb3000cc81adabb015ee069441aa",
    ("A", "user"): "e46a3f0e53edf4f11c6dfbc7369bcbc5ebf502644c1603259099551def15e7a4",
    ("B", "system"): "21059d9ae9faebcf4b8457254fbce34c1a035059c144b1c782e757951c724db4",
    ("B", "user"): "b6a0d99e4c6bcf3020614fe06e96c92d287eb10382359bccf7a12fadeced84bf",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _view(role: str) -> AgentView:
    state = _build_state(seed=31)
    return AgentView(role=role, resource_pool=state.resource_pool, own_valuation=state.valuation_A,
                     transcript_so_far=[], round_number=1, rounds_remaining=9, max_rounds=10)


# ------------------------------------------------------------ baseline
@pytest.mark.parametrize("role", ["A", "B"])
def test_baseline_prompts_are_byte_identical_to_frozen(role):
    view = _view(role)
    assert _sha(SYSTEM_INSTRUCTIONS) == GOLDEN["template"]
    assert _sha(system_instructions(view)) == GOLDEN[(role, "system")]
    assert _sha(system_instructions(view, "baseline_v1")) == GOLDEN[(role, "system")]
    assert _sha(build_prompt(view)) == GOLDEN[(role, "user")]


def test_exactly_two_variants_exist():
    assert set(INSTRUCTION_VARIANTS) == {"baseline_v1", "structured_v1"}
    assert INSTRUCTION_VARIANTS["baseline_v1"] is None


# ---------------------------------------------------------- structured
@pytest.mark.parametrize("role", ["A", "B"])
def test_structured_is_baseline_plus_exactly_one_block(role):
    view = _view(role)
    baseline = system_instructions(view, "baseline_v1")
    structured = system_instructions(view, "structured_v1")
    assert structured != baseline
    assert structured == baseline + "\n\n" + STRUCTURED_V1_BLOCK
    # The user-turn prompt takes no variant: identical information either way.
    assert "variant" not in build_prompt.__code__.co_varnames


def test_structured_block_has_the_three_approved_components():
    block = STRUCTURED_V1_BLOCK
    steps = [line for line in block.splitlines() if line[:2] in ("1.", "2.", "3.")]
    assert len(steps) == 3
    ranking, trades, disagreement = steps
    assert ranking.startswith("1. Preference ranking:")
    assert "matter most" in ranking and "least" in ranking and "ask the other agent" in ranking
    assert trades.startswith("2. Integrative trades:")
    assert "value less" in trades and "value more" in trades
    assert disagreement.startswith("3. Disagreement point and deadline:")
    assert "WALK_AWAY" in disagreement and "final" in disagreement
    assert "zero value" in disagreement and "no agreement" in disagreement


def test_structured_block_has_no_fairness_instruction():
    lowered = STRUCTURED_V1_BLOCK.lower()
    for term in ("fair", "equal", "equit", "envy", "even split", "split evenly", "half", "50/50", "justice"):
        assert term not in lowered, term


def test_unknown_variant_is_rejected():
    with pytest.raises(ValueError, match="Unknown instruction variant"):
        system_instructions(_view("A"), "structured_v2")
    for cls in (ClaudeAgent, OpenAIAgent):
        with pytest.raises(ValueError, match="Unknown instruction variant"):
            cls(CFG, client=StubClient(), instructions_variant="plain")


# ------------------------------------------- the agent sends its variant
def _sent_system(kind, client) -> str:
    sent = client.calls[0]
    return sent["system"] if kind == "claude" else sent["messages"][0]["content"]


def _sent_user(kind, client) -> str:
    sent = client.calls[0]
    return sent["messages"][0]["content"] if kind == "claude" else sent["messages"][1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_agent_sends_its_own_variant_and_identical_user_prompt(kind):
    raw = {"action_type": "WALK_AWAY", "allocation": None, "message": None}
    default_agent, default_client = make_agent(kind, wrap(kind, raw))
    cls = ClaudeAgent if kind == "claude" else OpenAIAgent
    structured_client = StubClient(wrap(kind, raw))
    structured_agent = cls(CFG, client=structured_client, instructions_variant="structured_v1")

    assert default_agent.instructions_variant == "baseline_v1"
    assert structured_agent.instructions_variant == "structured_v1"
    _, state = await run_first_turn(default_agent)
    await run_first_turn(structured_agent)

    view = AgentView(role="A", resource_pool=state.resource_pool, own_valuation=state.valuation_A,
                     transcript_so_far=[], round_number=1, rounds_remaining=2, max_rounds=3)
    assert _sent_system(kind, default_client) == system_instructions(view, "baseline_v1")
    assert _sent_system(kind, structured_client) == system_instructions(view, "structured_v1")
    assert _sent_user(kind, default_client) == _sent_user(kind, structured_client)
    # Everything else in the request is identical too (schema, settings).
    strip = lambda kw: {k: v for k, v in kw.items() if k not in ("system", "messages")}
    assert strip(default_client.calls[0]) == strip(structured_client.calls[0])


# ------------------------------------------ label must match the prompt
def _cfg(method: str) -> ExperimentConfig:
    return ExperimentConfig(mode="smoke", method=method, model_A_label="a", model_B_label="b",
                            num_negotiations=1, base_seed=900, max_rounds=4)


def _agents(variant_a: str, variant_b: str):
    walk = wrap("claude", {"action_type": "WALK_AWAY"})
    gpt_walk = wrap("openai", {"action_type": "WALK_AWAY", "allocation": None, "message": None})
    return (ClaudeAgent(CFG, client=StubClient(walk, walk), instructions_variant=variant_a),
            OpenAIAgent(CFG, client=StubClient(gpt_walk, gpt_walk), instructions_variant=variant_b))


@pytest.mark.asyncio
@pytest.mark.parametrize("method,variants", [
    ("structured_v1", ("baseline_v1", "baseline_v1")),    # claims structured, sends baseline
    ("structured_v1", ("structured_v1", "baseline_v1")),  # one side mismatched
    ("baseline_v1", ("structured_v1", "structured_v1")),  # claims baseline, sends structured
    ("baseline_plain_prompting", ("structured_v1", "structured_v1")),  # structured under another label
])
async def test_mismatched_condition_label_is_refused_before_any_call(tmp_path, method, variants):
    a, b = _agents(*variants)
    db = tmp_path / "r.db"
    with pytest.raises(ValueError, match="does not match agent"):
        await run_batch(_cfg(method), a, b, db_path=db)
    assert a._client.calls == [] and b._client.calls == []
    assert not db.exists()  # refused before a run row was written


@pytest.mark.asyncio
@pytest.mark.parametrize("method,variant", [
    ("structured_v1", "structured_v1"),
    ("baseline_v1", "baseline_v1"),
    ("baseline_plain_prompting", "baseline_v1"),  # legacy label, baseline prompt: allowed
])
async def test_matching_label_runs_and_records_variant_and_prompt_hash(tmp_path, method, variant):
    a, b = _agents(variant, variant)
    db = tmp_path / "r.db"
    records = await run_batch(_cfg(method), a, b, db_path=db)
    assert len(records) == 1 and records[0].method == method

    conn = sqlite3.connect(db)
    config_json, prompt_hash, run_method = conn.execute(
        "SELECT config_json, prompt_hash, method FROM runs").fetchone()
    agents = json.loads(config_json)["agents"]
    assert run_method == method
    assert agents["A"]["params"]["instructions_variant"] == variant
    assert agents["B"]["params"]["instructions_variant"] == variant
    # prompt_hash covers prompting.py (both variants' text) + both schemas;
    # together with the recorded variant it identifies the exact prompt sent.
    assert prompt_hash == provenance.prompt_hash()


@pytest.mark.asyncio
async def test_mock_agents_send_no_prompt_and_are_not_blocked(tmp_path):
    a, b = MockAgent(name="m-a"), MockAgent(name="m-b")
    records = await run_batch(_cfg("structured_v1"), a, b, db_path=tmp_path / "r.db")
    assert len(records) == 1
