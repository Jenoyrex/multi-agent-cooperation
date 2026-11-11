"""Model connections, keys, consent, caps. Everything runs against a fake server on 127.0.0.1 or a stub client."""
import asyncio
import json
import re
import sys
import threading
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from dashboard import server as SV
from dashboard.data import Store
from sandbox import experiment as X, keys, policy, providers
from sandbox.model_agent import PromptAgent
from src.agents.base import AgentView
from src.agents.schema import InvalidAgentOutputError, TransportError
from tests.sandbox_fake import TOKENS, FakeModels
from workbench import agents as A, export, simulations as S, store
from workbench.agents import WorkbenchError

DUMMY_KEY = "sk-DUMMYKEY0123456789abcdefTESTONLY"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "w.db")
    monkeypatch.chdir(tmp_path)  # no stray .env
    for name in keys.ENV_VARS.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sandbox.model_agent.BACKOFF_S", 0.0)
    keys.forget_all()
    policy.set_request_local(True)
    yield
    policy.set_request_local(True)
    keys.forget_all()


@pytest.fixture
def fake():
    f = FakeModels()
    yield f
    f.close()


def connection(fake, tier="local", **kw):
    doc, _ = __import__("sandbox.api", fromlist=["x"])._save_connection({"name": f"fake {tier}", "tier": tier, "base_url": fake.url, "structured": "json_object", **kw})
    return doc["id"]


def agent(conn, model="good", **params):
    d, _ = __import__("workbench.api", fromlist=["x"])._save("agents", A.BUILTIN, A.validate)(
        {"name": f"model {model}", "kind": "prompt", "params": {"connection": conn, "model": model, "max_output_tokens": 100, "timeout_s": 2, **params}})
    return d["id"]


def design(x, y="builtin-priority", **kw):
    return {"name": "t", "environment": "builtin-field-hospital", "agents": {"X": x, "Y": y}, "conditions": ["baseline_v1"], "repetitions": 1,
            "swap_seats": False, "swap_first_mover": False, **kw}


def consented(req):
    cfg = S.validate(req)
    return {**req, "consent": {"token": X.consent_token(cfg), "accepted": X.required_consents(cfg)}}


def run(req):
    sim = S.start(req, background=False)
    return S.get(sim.id)


# ---------------------------------------------------------------- local and BYO-key agents
def test_local_model_agent_plays_a_full_negotiation_and_is_labelled(fake):
    doc = run(design(agent(connection(fake))))
    t = doc["trials"][0]
    assert doc["status"] == "completed" and t["status"] == "completed" and t["outcome"] in ("agreed", "walked_away", "timeout")
    assert doc["config"]["data_kind"] == "local-model" and doc["config"]["source_labels"]["X"] == "Local model: good"
    assert doc["config"]["label"].startswith("LOCAL MODEL: ") and "Not the research study" in doc["config"]["label"]
    assert doc["config"]["source_labels"]["Y"] == "Rule-based (simulated)"
    calls = t["usage"]["api_calls"]
    assert calls == len(fake.chats()) > 0
    assert t["usage"]["input_tokens"] == calls * TOKENS[0] and t["usage"]["output_tokens"] == calls * TOKENS[1]
    assert t["usage"]["cost_usd"] is None  # no price given: unknown, never zero


def test_byo_key_agent_needs_consent_then_uses_the_key_and_records_cost(fake):
    conn = connection(fake, tier="api")
    aid = agent(conn)
    keys.set_key(conn, DUMMY_KEY)
    req = design(aid, prices={"good": [2.0, 10.0]}, caps={"max_tokens": 100_000})
    before = len(fake.requests)
    with pytest.raises(WorkbenchError) as e:
        S.start(req, background=False)
    assert e.value.field == "consent" and len(fake.requests) == before
    doc = run(consented(req))
    assert doc["status"] == "completed" and doc["config"]["data_kind"] == "api-model"
    assert doc["config"]["source_labels"]["X"] == "API model: fake api/good"
    assert all(r["auth"] == DUMMY_KEY for r in fake.chats())
    u = doc["trials"][0]["usage"]
    assert u["cost_usd"] == pytest.approx(u["api_calls"] * (TOKENS[0] * 2 + TOKENS[1] * 10) / 1e6)


def test_api_model_refuses_without_a_key_before_sending_anything(fake):
    aid = agent(connection(fake, tier="api"))
    with pytest.raises(WorkbenchError, match="add your API key"):
        S.validate(design(aid))
    assert fake.requests == []


def test_consent_is_for_this_exact_design(fake):
    conn = connection(fake, tier="api")
    aid = agent(conn)
    keys.set_key(conn, DUMMY_KEY)
    req = consented(design(aid, caps={"max_tokens": 100_000}))
    bigger = {**req, "repetitions": 5}
    with pytest.raises(WorkbenchError) as e:
        S.start(bigger, background=False)
    assert e.value.status == 409
    with pytest.raises(WorkbenchError, match="you pay"):
        S.start({**req, "consent": {**req["consent"], "accepted": []}}, background=False)
    assert fake.chats() == []


def test_a_token_cap_is_mandatory_for_api_models_and_prices_are_never_invented(fake):
    conn = connection(fake, tier="api")
    aid = agent(conn)
    keys.set_key(conn, DUMMY_KEY)
    with pytest.raises(WorkbenchError, match="token cap") as e:
        S.start(consented(design(aid)), background=False)
    assert e.value.field == "caps.max_tokens"
    with pytest.raises(WorkbenchError, match="too small"):
        S.start(consented(design(aid, caps={"max_tokens": 10})), background=False)
    with pytest.raises(WorkbenchError, match="needs your prices"):
        S.start(consented(design(aid, caps={"max_tokens": 100_000, "max_cost": 1.0})), background=False)
    est = X.estimate(S.validate(design(aid)))
    assert est["cost_estimate"] is None and est["missing_prices"] == ["good"] and est["agents"][0]["price"] is None
    priced = X.estimate(S.validate(design(aid, prices={"good": [1.0, 1.0]})))
    assert priced["cost_estimate"] > 0 and priced["api_tokens_estimate"] > 0


def test_cap_reached_stops_cleanly_with_no_call_after_it(fake):
    conn = connection(fake, tier="api")
    aid = agent(conn)
    keys.set_key(conn, DUMMY_KEY)
    cfg = S.validate(design(aid, repetitions=20))
    reserve = X.estimate(cfg)["reserve_per_call"]
    cap = reserve + 3 * sum(TOKENS)  # room for about three calls, then the reserve no longer fits
    doc = run(consented(design(aid, repetitions=20, caps={"max_tokens": cap})))
    assert doc["status"] == "cancelled" and "cap was reached" in doc["stop_reason"]
    assert doc["trials"][-1]["status"] == "aborted" and doc["trials"][-1]["reason"] == "budget_exhausted"
    assert len(doc["trials"]) < 20
    assert len(fake.chats()) <= 4
    assert sum(TOKENS) * len(fake.chats()) <= cap


# ---------------------------------------------------------------- failures
@pytest.mark.parametrize("model,reason", [("garbage", "malformed_output"), ("fenced", "malformed_output"), ("truncated", "malformed_output"),
                                          ("too-many", "invalid_allocation")])
def test_bad_output_is_invalid_action_and_is_never_repaired(fake, model, reason):
    doc = run(design(agent(connection(fake), model), swap_first_mover=False))
    t = doc["trials"][0]
    assert t["status"] == "completed" and t["outcome"] == "invalid_action" and t["invalid_reason"] == reason


def test_timeout_aborts_it_is_not_an_outcome(fake):
    doc = run(design(agent(connection(fake), "slow", timeout_s=1)))
    t = doc["trials"][0]
    assert t["status"] == "aborted" and t["reason"] == "transport_failure" and "outcome" not in t


def test_rate_limit_is_retried_and_counted(fake):
    t = run(design(agent(connection(fake), "flaky")))["trials"][0]
    assert t["status"] == "completed" and t["usage"]["api_attempts"] == t["usage"]["api_calls"] + 1


def test_repeated_connection_failures_stop_the_experiment(fake):
    doc = run(design(agent(connection(fake), "unauthorized"), repetitions=10))
    assert doc["status"] == "cancelled" and len(doc["trials"]) == S.MAX_FAILURES_IN_A_ROW and "failures in a row" in doc["stop_reason"]


# ---------------------------------------------------------------- test connection, model list
def test_connection_test_sends_one_tiny_request_only_when_clicked(fake):
    conn = connection(fake)
    providers.listing()
    assert fake.requests == []
    r = providers.test_connection(conn, "good")
    assert r["ok"] and r["input_tokens"] == TOKENS[0] and len(fake.requests) == 1
    assert fake.requests[0]["body"]["max_tokens"] == 16


@pytest.mark.parametrize("model,kind", [("unauthorized", "invalid_key"), ("limited", "rate_limited")])
def test_connection_test_explains_failures(fake, model, kind):
    r = providers.test_connection(connection(fake), model)
    assert not r["ok"] and r["kind"] == kind and r["hint"]


def test_connection_test_timeout_and_unreachable_and_not_compatible(fake, monkeypatch):
    bad = providers.test_connection(connection(fake), "not-json-http")
    assert not bad["ok"]
    conn = connection(fake)
    store.put("connections", {**store.get("connections", conn), "base_url": "http://127.0.0.1:9/v1"})
    r = providers.test_connection(conn, "good")
    assert not r["ok"] and r["kind"] == "local_not_running" and "is not running" in r["title"] and "Start" in r["hint"]


def test_model_list_comes_from_the_server(fake):
    assert providers.list_models(connection(fake))["models"] == ["good", "other"]


def test_base_url_rules():
    assert policy.clean_base_url("http://localhost:11434/v1/", True) == "http://localhost:11434/v1"
    assert policy.clean_base_url("https://api.groq.com/openai/v1", False)
    for url, local in (("https://api.openai.com/v1", True), ("http://api.example.com/v1", False), ("https://user:pw@example.com/v1", False),
                       ("https://example.com/v1?key=abc", False), ("ftp://example.com", False), ("not a url", False), ("", False)):
        with pytest.raises(WorkbenchError):
            policy.clean_base_url(url, local)


def test_presets_match_the_documented_urls():
    assert providers.PRESETS["ollama"]["base_url"] == "http://localhost:11434/v1"
    assert providers.PRESETS["lmstudio"]["base_url"] == "http://localhost:1234/v1"
    assert providers.PRESETS["gemini"]["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert {p["tier"] for k, p in providers.PRESETS.items() if k in ("ollama", "lmstudio")} == {"local"}


# ---------------------------------------------------------------- Anthropic, stub client
VIEW = AgentView(role="A", resource_pool={"food": 10, "water": 4}, own_valuation={"food": 6.0, "water": 10.0}, transcript_so_far=[],
                 round_number=1, rounds_remaining=9, max_rounds=10)


class StubAnthropic:
    def __init__(self, text='{"action_type": "OFFER", "allocation": {"food": 5, "water": 2}, "message": "Half."}', stop="end_turn", error=None):
        self.calls, self.text, self.stop, self.error = [], text, stop, error
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(model="claude-stub", stop_reason=self.stop, usage=SimpleNamespace(input_tokens=11, output_tokens=7),
                               content=[SimpleNamespace(type="text", text=self.text)])


def _claude(stub, **kw):
    return PromptAgent("c", providers.resolve("anthropic"), "claude-stub", "baseline_v1", client=stub, backoff_s=0.0, **kw)


def test_anthropic_request_uses_structured_output_not_a_forced_tool():
    stub = StubAnthropic()
    agent_ = _claude(stub, persona="Be brief.")
    action = asyncio.run(agent_.generate_response(VIEW))
    assert action.action_type == "OFFER" and action.allocation == {"A": {"food": 5, "water": 2}, "B": {"food": 5, "water": 2}}
    kw = stub.calls[0]
    assert kw["output_config"]["format"]["type"] == "json_schema" and "tool_choice" not in kw and "tools" not in kw
    assert "temperature" not in kw and "extra_body" not in kw  # sent only when the user sets it
    assert "Be brief." in kw["system"]
    assert (agent_.last_call.input_tokens, agent_.last_call.output_tokens) == (11, 7)
    asyncio.run(_claude(stub := StubAnthropic(), temperature=0.3).generate_response(VIEW))
    assert stub.calls[0]["extra_body"] == {"temperature": 0.3}


@pytest.mark.parametrize("stub", [StubAnthropic(text="not json"), StubAnthropic(stop="max_tokens"), StubAnthropic(stop="refusal"), StubAnthropic(text="")])
def test_anthropic_bad_replies_are_invalid(stub):
    with pytest.raises(InvalidAgentOutputError):
        asyncio.run(_claude(stub).generate_response(VIEW))


def test_anthropic_errors_are_transport_errors_and_redacted():
    class Err(Exception):
        status_code = 401
    keys.set_key("anthropic", DUMMY_KEY)
    a = _claude(StubAnthropic(error=Err(f"bad key {DUMMY_KEY}")))
    with pytest.raises(TransportError) as e:
        asyncio.run(a.generate_response(VIEW))
    assert DUMMY_KEY not in str(e.value) and DUMMY_KEY not in a.last_call.error


# ---------------------------------------------------------------- keys never leave
def _search(data, where):
    assert DUMMY_KEY not in data and DUMMY_KEY[:20] not in data, f"key found in {where}"


def test_dummy_key_appears_nowhere(fake, tmp_path, capfd):
    conn = connection(fake, tier="api")
    keys.set_key(conn, DUMMY_KEY)
    good, loud, bad = agent(conn, "echo-key"), agent(conn), agent(conn, "unauthorized")
    finished = [run(consented(design(good, caps={"max_tokens": 100_000}))), run(consented(design(loud, caps={"max_tokens": 100_000}))),
                run(consented(design(bad, repetitions=3, caps={"max_tokens": 100_000})))]
    assert any(r["auth"] == DUMMY_KEY for r in fake.requests)  # it really was used
    assert providers.test_connection(conn, "unauthorized")["ok"] is False
    # database, as raw bytes
    _search((tmp_path / "w.db").read_bytes().decode("latin-1"), "the database")
    assert all("key" not in c for c in store.all_("connections"))
    # every export, and the responses
    for doc in finished:
        for name in export.FILES:
            _search(json.dumps(export.build(doc, name)), f"export {name}")
    from workbench import api as WA
    for s in finished:
        _search(json.dumps(WA.get(f"/api/workbench/simulations/{s['id']}")[0]), "the API")
    _search(json.dumps(providers.listing()), "the connection list")
    assert providers.public(providers.resolve(conn))["key"] == f"sk-…{DUMMY_KEY[-4:]}"
    # logs
    captured = capfd.readouterr()
    _search(captured.out + captured.err, "the logs")
    assert keys.redact(f"oops {DUMMY_KEY} and sk-abc***xyz1") == "oops [redacted key] and [redacted key]"


# ---------------------------------------------------------------- key endpoints over HTTP
@pytest.fixture
def server(tmp_path):
    SV.Handler.store = Store(tmp_path / "results")
    httpd = SV.LabServer(("127.0.0.1", 0), partial(SV.Handler, directory=str(SV.STATIC)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def http(port, path, body=None, host=None, origin="same"):
    host = host or f"127.0.0.1:{port}"
    headers = {"Host": host, "Content-Type": "application/json"}
    if origin:
        headers["Origin"] = f"http://{host}" if origin == "same" else origin
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_key_routes_answer_this_computer_same_origin_and_never_return_the_key(server):
    code, text = http(server, "/api/models/keys", {"connection": "openai", "key": DUMMY_KEY})
    assert code == 200 and DUMMY_KEY not in text and json.loads(text)["key"] == f"sk-…{DUMMY_KEY[-4:]}"
    assert keys.get("openai") == DUMMY_KEY
    code, text = http(server, "/api/models/connections")
    assert code == 200 and DUMMY_KEY not in text
    # a page elsewhere, a missing Origin, a rebinding Host
    assert http(server, "/api/models/keys", {"connection": "openai", "key": "sk-otherkey12345678"}, origin="https://evil.example")[0] == 403
    assert http(server, "/api/models/keys", {"connection": "openai", "key": "sk-otherkey12345678"}, origin=None)[0] == 403
    assert http(server, "/api/models/keys", {"connection": "openai", "key": "sk-otherkey12345678"}, host="evil.example")[0] == 403
    assert http(server, "/api/models/connections", host="evil.example")[0] == 403
    assert keys.get("openai") == DUMMY_KEY
    code, text = http(server, "/api/models/keys/delete", {"connection": "openai"})
    assert code == 200 and json.loads(text)["key"] is None and keys.get("openai") == ""


def test_a_connection_never_stores_a_key(server):
    code, _ = http(server, "/api/models/connections", {"name": "x", "tier": "api", "base_url": "https://a.example/v1", "api_key": DUMMY_KEY})
    assert code == 400


def test_non_loopback_client_is_refused():
    fake_handler = SimpleNamespace(client_address=("10.0.0.5", 1234), _host_kind=lambda: "local")
    assert SV.Handler._from_this_computer(fake_handler) is False
    fake_handler.client_address = ("127.0.0.1", 1234)
    assert SV.Handler._from_this_computer(fake_handler) is True
    fake_handler._host_kind = lambda: "public"
    assert SV.Handler._from_this_computer(fake_handler) is False


def test_env_and_dotenv_keys_are_read_without_entering_the_environment(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={DUMMY_KEY}\n")
    import os
    assert keys.get("gemini") == DUMMY_KEY and keys.source("gemini") == "environment"
    assert "GEMINI_API_KEY" not in os.environ
    keys.remove("gemini")
    assert keys.get("gemini") == "" and keys.source("gemini") is None
    assert keys.redact(DUMMY_KEY) == "[redacted key]"


def test_a_request_from_elsewhere_cannot_use_local_features(fake):
    conn = connection(fake)
    aid = agent(conn)
    policy.set_request_local(False)
    with pytest.raises(WorkbenchError) as e:
        S.validate(design(aid))
    assert e.value.status == 403
    with pytest.raises(WorkbenchError):
        providers.test_connection(conn, "good")
    assert fake.requests == []
