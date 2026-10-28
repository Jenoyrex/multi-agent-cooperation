"""Zero-cost mode fails closed: no real provider client is built and no agent
request leaves the machine unless ALLOW_PAID_PROVIDER_CALLS=1 (never set in
tests, see conftest). Every test runs inside the network tripwire, so even a
missed path could not reach the network."""
import asyncio
import runpy
import socket
from pathlib import Path

import pytest

from lab import adapters
from src import zero_cost
from src.agents.claude_agent import ClaudeAgent
from src.agents.generation import GenerationConfig
from src.agents.openai_agent import OpenAIAgent
from src.experiments.config import APPROVED_OPERATIONAL, APPROVED_SCIENTIFIC
from src.experiments.pilot import run_pilot
from src.zero_cost import ZeroCostViolation, network_tripwire
from tests.test_agents_stub import run_first_turn

ROOT = Path(__file__).resolve().parent.parent
CLAUDE = GenerationConfig(**APPROVED_SCIENTIFIC["ClaudeAgent"], **APPROVED_OPERATIONAL)
OPENAI = GenerationConfig(**APPROVED_SCIENTIFIC["OpenAIAgent"], **APPROVED_OPERATIONAL)


@pytest.fixture(autouse=True)
def tripwire():
    with network_tripwire() as blocked:
        yield blocked


@pytest.fixture
def sdk_constructors(monkeypatch):
    """Replace both SDK client classes with recorders, so a test fails if a
    real client were ever constructed."""
    import anthropic
    import openai
    built = []
    for module, name in ((anthropic, "AsyncAnthropic"), (openai, "AsyncOpenAI")):
        monkeypatch.setattr(module, name, lambda *a, _n=name, **k: built.append(_n))
    return built


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [ClaudeAgent(CLAUDE), OpenAIAgent(OPENAI)], ids=["claude", "openai"])
async def test_a_real_provider_client_is_never_built(agent, sdk_constructors, tripwire):
    with pytest.raises(ZeroCostViolation, match="real (Anthropic|OpenAI) client"):
        await run_first_turn(agent)
    assert sdk_constructors == [] and tripwire == []   # refused before the SDK, not by the tripwire


@pytest.mark.asyncio
async def test_the_paid_pilot_is_refused_before_any_file_exists(tmp_path, sdk_constructors):
    db = tmp_path / "pilot.db"
    with pytest.raises(ZeroCostViolation, match="paid pilot"):
        await run_pilot(CLAUDE, OPENAI, db, max_cost_usd=20.0, max_input_tokens_per_call=20000)
    assert sdk_constructors == [] and list(tmp_path.iterdir()) == []


def test_the_real_smoke_test_script_is_refused_before_any_agent(sdk_constructors):
    script = runpy.run_path(str(ROOT / "scripts" / "run_real_smoke_test.py"), run_name="not_main")
    with pytest.raises(ZeroCostViolation, match="real-model smoke test"):
        asyncio.run(script["main"]())
    assert sdk_constructors == []


@pytest.mark.asyncio
async def test_lab_agents_cannot_leave_the_machine(sdk_constructors, tripwire):
    remote = adapters.HTTPAgent("https://agent.example.org/turn", "remote", "baseline_v1", token="t")
    with pytest.raises(ZeroCostViolation, match="HTTP agent request to non-loopback host"):
        await run_first_turn(remote)
    assert tripwire == []   # refused before any DNS lookup or connection
    for kind in ("claude", "openai"):  # the Lab's provider agents go through the SDK gate
        agent = adapters.build_agent({"kind": kind}, None, "baseline_v1")
        with pytest.raises(ZeroCostViolation):
            await run_first_turn(agent)
    probe = await adapters.probe({"kind": "http", "url": "https://agent.example.org/turn", "label": "x",
                                  "timeout_s": 5}, None)
    assert probe == {"ok": False, "title": "Probe failed", "message": "ZeroCostViolation"}
    assert sdk_constructors == [] and tripwire == []


def test_tripwire_blocks_non_loopback_and_restores_the_socket_module():
    original = (socket.socket.connect, socket.getaddrinfo)
    with network_tripwire() as blocked:
        with pytest.raises(ZeroCostViolation):
            socket.create_connection(("192.0.2.1", 443), timeout=0.1)   # TEST-NET-1
        with pytest.raises(ZeroCostViolation):
            socket.getaddrinfo("api.anthropic.com", 443)
        socket.getaddrinfo("localhost", 0)                              # loopback stays allowed
        assert blocked == [("getaddrinfo", "192.0.2.1"), ("getaddrinfo", "api.anthropic.com")]
        socket.getaddrinfo(b"localhost", 0)                             # bytes, as httpx passes them
    with network_tripwire():
        pass
    assert (socket.socket.connect, socket.getaddrinfo) == original      # leaving restores what was there
    with network_tripwire() as blocked:
        s = socket.socket()
        with pytest.raises(ZeroCostViolation):
            s.connect(("192.0.2.1", 443))
        s.close()
        assert blocked == [("connect", ("192.0.2.1", 443))]


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 2"])
def test_only_the_exact_opt_in_value_unlocks_paid_calls(monkeypatch, value):
    monkeypatch.setenv(zero_cost.ENV, value)
    with pytest.raises(ZeroCostViolation):
        zero_cost.require_paid_calls("x")
    monkeypatch.setenv(zero_cost.ENV, "1")
    zero_cost.require_paid_calls("x")


@pytest.mark.asyncio
async def test_even_with_the_opt_in_the_tripwire_stops_a_real_sdk_call(monkeypatch, tripwire):
    """Second layer: a real Anthropic client, unlocked, still cannot reach the
    provider inside the tripwire (fake key; max_retries=0 keeps it fast)."""
    monkeypatch.setenv(zero_cost.ENV, "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    cfg = GenerationConfig(**{**CLAUDE.to_dict(), "max_retries": 0})
    record, _ = None, None
    with pytest.raises(Exception):
        record, _ = await run_first_turn(ClaudeAgent(cfg))
    assert record is None and ("getaddrinfo", "api.anthropic.com") in tripwire


def test_tripwire_fails_invalid_names_like_the_real_resolver():
    """The real getaddrinfo rejects an invalid IDNA name locally (UnicodeError,
    no query); the tripwire must too, so code that handles that error keeps working."""
    with network_tripwire() as blocked:
        for bad in ("a..b", "a" * 64 + ".example"):
            with pytest.raises(UnicodeError):
                socket.getaddrinfo(bad, 80)
        assert blocked == []
