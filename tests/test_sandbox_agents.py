"""Agent import: HTTP (local and remote) and Python file agents play full negotiations; no agent can reach the other's values;
a Python file cannot read API keys from its environment; the public demo locks all of it out."""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dashboard import demo
from lab.adapters import HTTPAgent
from sandbox import experiment as X, keys, policy, providers
from sandbox.python_agent import PythonFileAgent, child_env
from src import zero_cost
from src.agents.schema import TransportError
from tests.sandbox_fake import FakeModels
from tests.test_sandbox_models import DUMMY_KEY, agent, connection, consented, design, http, isolated, run, server  # noqa: F401
from workbench import agents as A, api as WA, simulations as S, store
from workbench.agents import WorkbenchError

FAIR = """
from src.agents.base import Agent
from src.agents.schema import NegotiationAction

class Fair(Agent):
    name = "fair"
    async def generate_response(self, view):
        print("agents may print; it must not break the protocol")
        last = view.transcript_so_far[-1] if view.transcript_so_far else None
        if last and last.actor != view.role and last.action.action_type == "OFFER":
            return NegotiationAction(action_type="ACCEPT", message="ok")
        other = "B" if view.role == "A" else "A"
        mine = {c: q // 2 for c, q in view.resource_pool.items()}
        return NegotiationAction(action_type="OFFER", allocation={view.role: mine, other: {c: q - mine[c] for c, q in view.resource_pool.items()}})
"""
DICT_FORM = """
class Plain:
    async def generate_response(self, view):
        if view.transcript_so_far and view.transcript_so_far[-1].actor != view.role:
            return {"action_type": "ACCEPT"}
        return {"action_type": "OFFER", "allocation": {c: q // 2 for c, q in view.resource_pool.items()}}
"""


def python_agent(source=FAIR, cls="Fair", timeout=5, name="py"):
    d, _ = WA._save("agents", A.BUILTIN, A.validate)({"name": name, "kind": "python_file", "params": {"class_name": cls, "timeout_s": timeout},
                                                      "upload": {"filename": "agent.py", "source": source}})
    return d["id"]


# ---------------------------------------------------------------- python file agents
def test_python_file_needs_explicit_consent_then_completes_a_negotiation():
    aid = python_agent()
    with pytest.raises(WorkbenchError, match="Run this file on my computer"):
        S.start({**consented(design(aid)), "consent": {"token": X.consent_token(S.validate(design(aid))), "accepted": []}}, background=False)
    doc = run(consented(design(aid)))
    t = doc["trials"][0]
    assert t["status"] == "completed" and t["outcome"] == "agreed", t
    assert doc["config"]["source_labels"]["X"] == "Python file: agent.py" and doc["config"]["data_kind"] == "local-agents"
    assert "user_agent" not in sys.modules  # the server process never imported it


def test_python_file_returning_the_http_dict_form_works():
    t = run(consented(design(python_agent(DICT_FORM, "Plain"))))["trials"][0]
    assert t["status"] == "completed" and t["outcome"] == "agreed"


def test_python_file_cannot_read_api_keys_from_its_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("SOME_OTHER_SECRET", DUMMY_KEY)
    assert DUMMY_KEY not in json.dumps(child_env())
    out = tmp_path / "env.json"
    spy = f"""
import json, os
class Spy:
    async def generate_response(self, view):
        with open({str(out)!r}, "w") as f:
            json.dump(dict(os.environ), f)
        return {{"action_type": "WALK_AWAY"}}
"""
    t = run(consented(design(python_agent(spy, "Spy"))))["trials"][0]
    assert t["status"] == "completed"
    seen = json.loads(out.read_text())
    assert DUMMY_KEY not in json.dumps(seen) and not any("API_KEY" in k or "SECRET" in k for k in seen)


def test_hanging_python_file_is_stopped_and_is_not_an_outcome():
    hang = "import time\nclass Hang:\n    async def generate_response(self, view):\n        time.sleep(600)\n"
    t = run(consented(design(python_agent(hang, "Hang", timeout=1))))["trials"][0]
    assert t["status"] == "aborted" and t["reason"] == "transport_failure" and "stopped" in t["detail"]


def test_python_file_errors_become_invalid_action():
    boom = "class Boom:\n    async def generate_response(self, view):\n        raise RuntimeError('nope')\n"
    t = run(consented(design(python_agent(boom, "Boom"))))["trials"][0]
    assert t["status"] == "completed" and t["outcome"] == "invalid_action" and t["invalid_reason"] == "malformed_output"
    crash = "import os\nclass Crash:\n    async def generate_response(self, view):\n        os._exit(3)\n"
    t = run(consented(design(python_agent(crash, "Crash"))))["trials"][0]
    assert t["status"] == "aborted"


# ---------------------------------------------------------------- HTTP agents
class Endpoint:
    """A small HTTP agent. Records every observation it receives."""

    def __init__(self, host="127.0.0.1"):
        self.seen = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                obs = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.seen.append(obs)
                reply = ({"action_type": "ACCEPT"} if "ACCEPT" in obs["allowed_actions"] else
                         {"action_type": "OFFER", "allocation": {c: q // 2 for c, q in obs["resource_pool"].items()}})
                body = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer((host, 0), H)
        self.url = f"http://{host}:{self.server.server_address[1]}/turn"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def endpoint():
    e = Endpoint()
    yield e
    e.close()


def http_agent(url, kind="http_local"):
    d, _ = WA._save("agents", A.BUILTIN, A.validate)({"name": kind, "kind": kind, "params": {"url": url, "timeout_s": 5}})
    return d["id"]


def test_local_http_agent_completes_a_negotiation_without_consent(endpoint):
    doc = run(design(http_agent(endpoint.url)))
    assert doc["trials"][0]["status"] == "completed" and doc["config"]["source_labels"]["X"] == "HTTP agent on this computer"


def test_remote_http_rules():
    with pytest.raises(WorkbenchError, match="on this computer"):
        A.validate({"name": "r", "kind": "http_remote", "params": {"url": "http://127.0.0.1:8001/turn"}})
    with pytest.raises(WorkbenchError, match="credentials"):
        A.validate({"name": "r", "kind": "http_remote", "params": {"url": "https://u:p@example.org/turn"}})
    assert A.validate({"name": "r", "kind": "http_remote", "params": {"url": "https://example.org/turn"}})["kind"] == "http_remote"
    with pytest.raises(WorkbenchError, match="this machine"):  # the local kind stays loopback-only
        A.validate({"name": "r", "kind": "http_local", "params": {"url": "http://example.org/turn"}})


def test_remote_http_agent_needs_consent_and_completes_a_negotiation():
    # 127.0.0.2 is not in the zero-cost loopback list, so this takes the same path as a real remote address.
    try:
        e = Endpoint("127.0.0.2")
    except OSError:
        pytest.skip("this system has no 127.0.0.2")
    try:
        aid = http_agent(e.url, "http_remote")
        with pytest.raises(WorkbenchError) as err:
            S.start(design(aid), background=False)
        assert err.value.status == 409 and e.seen == []
        doc = run(consented(design(aid)))
        assert doc["trials"][0]["status"] == "completed", doc["trials"][0]
        assert doc["config"]["source_labels"]["X"].startswith("Remote HTTP agent: ") and e.seen
    finally:
        e.close()


def test_the_research_guard_still_refuses_remote_hosts_by_default():
    agent_ = HTTPAgent("http://example.org/turn", "x", "baseline_v1")
    with pytest.raises(zero_cost.ZeroCostViolation):
        agent_._post(b"{}", __import__("time").monotonic() + 1)
    assert not zero_cost.paid_calls_allowed()


# ---------------------------------------------------------------- privacy
ENV = {"name": "Private", "max_rounds": 6, "first_mover": "A", "resources": {"food": 10, "water": 10},
       "points": {"A": {"food": 37, "water": 63}, "B": {"food": 11, "water": 89}}}
A_ONLY, B_ONLY = ("3.70", "6.30"), ("1.10", "8.90")


def _env():
    d, _ = WA._save("environments", {}, __import__("workbench.environments", fromlist=["x"]).validate)(dict(ENV))
    return d["id"]


def _private_design(x, y, **kw):
    return {**design(x, y, **kw), "environment": _env()}


def _assert_only_own(text, seat):
    other = B_ONLY if seat == "A" else A_ONLY
    for value in other:
        assert value not in text, f"seat {seat} was shown the other agent's value {value}"


def test_prompt_agent_never_sees_the_other_agents_values():
    fake = FakeModels()
    try:
        doc = run(_private_design(agent(connection(fake), "good"), "builtin-priority", swap_seats=True))
        assert len(fake.chats()) > 0
        for req in fake.chats():
            text = json.dumps(req["body"])
            seat = "A" if "your turn, Agent A" in text else "B"
            _assert_only_own(text, seat)
            assert (A_ONLY if seat == "A" else B_ONLY)[0] in text  # it did get its own
    finally:
        fake.close()


def test_http_agent_never_sees_the_other_agents_values(endpoint):
    run(_private_design(http_agent(endpoint.url), "builtin-priority", swap_seats=True))
    assert endpoint.seen
    for obs in endpoint.seen:
        _assert_only_own(json.dumps(obs["own_valuation"]) + obs["prompt"], obs["role"])
        assert set(obs) >= {"own_valuation", "resource_pool"} and not ({"valuation_A", "valuation_B", "optimal_welfare", "seed"} & set(obs))


def test_python_file_never_sees_the_other_agents_values(tmp_path):
    out = tmp_path / "views.jsonl"
    spy = f"""
import dataclasses, json
class Spy:
    async def generate_response(self, view):
        with open({str(out)!r}, "a") as f:
            f.write(json.dumps({{"fields": [x.name for x in dataclasses.fields(view)], "role": view.role, "own": view.own_valuation}}) + chr(10))
        if view.transcript_so_far and view.transcript_so_far[-1].actor != view.role:
            return {{"action_type": "ACCEPT"}}
        return {{"action_type": "OFFER", "allocation": {{c: q // 2 for c, q in view.resource_pool.items()}}}}
"""
    run(consented(_private_design(python_agent(spy, "Spy"), "builtin-priority", swap_seats=True)))
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows
    for row in rows:
        assert set(row["fields"]) == {"role", "resource_pool", "own_valuation", "transcript_so_far", "round_number", "rounds_remaining", "max_rounds", "notices"}
        expected = ENV["points"][row["role"]]
        assert row["own"] == {c: expected[c] / 10 for c in expected}


# ---------------------------------------------------------------- public demo lockout
def test_public_demo_locks_out_every_local_feature(monkeypatch, server):  # noqa: F811
    fake = FakeModels()
    try:
        conn = connection(fake, tier="api")
        keys.set_key(conn, DUMMY_KEY)
        model, py, http_ = agent(conn), python_agent(), http_agent("http://127.0.0.1:9/turn")
        remote = http_agent("https://example.org/turn", "http_remote")
        before = len(fake.requests)
        monkeypatch.setenv(demo.ENV, "1")
        # routes do not exist
        for path, body in (("/api/models/keys", {"connection": "openai", "key": DUMMY_KEY}), ("/api/models/test", {"connection": conn, "model": "good"}),
                           ("/api/models/list", {"connection": conn}), ("/api/models/connections", {"name": "x", "tier": "local", "base_url": fake.url})):
            assert http(server, path, body)[0] == 405
        assert http(server, "/api/models/connections")[0] == 404
        # creating refused, kinds shown as locked
        for kind, params in (("prompt", {"connection": "ollama", "model": "m"}), ("http_remote", {"url": "https://example.org/turn"}),
                             ("python_file", {"class_name": "Fair"})):
            code, text = http(server, "/api/workbench/agents", {"name": "n", "kind": kind, "params": params})
            assert code == 403 and "public demo" in text
        kinds = {k["id"]: k for k in WA.catalog()["kinds"]}
        assert not any(kinds[k]["executable"] for k in A.LOCAL_ONLY) and WA.catalog()["local_features"] is False
        # starting refused, even with a saved agent or a consent
        for aid in (model, py, http_, remote):
            code, _ = http(server, "/api/workbench/simulations", consented_in_demo(aid))
            assert code == 403
        # and underneath: nothing can be built or called
        with pytest.raises(WorkbenchError):
            providers.client(providers.resolve(conn))
        with pytest.raises(WorkbenchError):
            providers.test_connection(conn, "good")
        with pytest.raises(WorkbenchError):
            providers.list_models(conn)
        with pytest.raises(WorkbenchError):
            PythonFileAgent("x", FAIR, "Fair")
        for aid in (model, http_, remote):
            with pytest.raises(WorkbenchError):
                A.build(store.get("agents", aid), "n", "baseline_v1")
        assert len(fake.requests) == before
        # still works: rule-based agents only, labelled
        assert http(server, "/api/workbench/simulations", design("builtin-priority", "builtin-concession"))[0] == 202
    finally:
        fake.close()


def consented_in_demo(aid):
    return {**design(aid), "consent": {"token": "x", "accepted": ["api_cost", "remote_http", "python_file"]}, "caps": {"max_tokens": 1000000}}


def test_remote_url_cannot_carry_a_secret_into_reports():
    for url in ("https://example.org/turn?token=abc", "https://example.org/turn#abc"):
        with pytest.raises(WorkbenchError, match="query"):
            A.validate({"name": "r", "kind": "http_remote", "params": {"url": url}})


def test_only_this_computer_can_save_a_python_file_to_run():
    policy.set_request_local(False)
    with pytest.raises(WorkbenchError) as e:
        python_agent()
    assert e.value.status == 403
    policy.set_request_local(True)
    assert python_agent()


def test_public_demo_host_runs_without_the_sdks_or_data_libraries():
    """render.yaml installs pydantic and python-dotenv only, so no AI client exists on the public host."""
    import subprocess
    code = """
import sys
for name in ("openai", "anthropic", "pandas", "matplotlib"):
    sys.modules[name] = None
import dashboard.server
from workbench import simulations as S, api
doc = S.get(S.start({"name": "t", "environment": "builtin-field-hospital", "agents": {"X": "builtin-priority", "Y": "builtin-concession"},
                     "conditions": ["baseline_v1"], "repetitions": 2, "swap_seats": False, "swap_first_mover": False}, background=False).id)
assert doc["status"] == "completed", doc
payload, status = api.get("/api/workbench/simulations/" + doc["id"])
assert status == 200 and payload["analytics"]["sandbox"]["profiles"]
print("ok")
"""
    env = {**os.environ, "PUBLIC_DEMO": "1", "PYTHONPATH": str(store.ROOT)}
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120, cwd=store.DB_PATH.parent)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-800:]
