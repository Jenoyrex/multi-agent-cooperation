"""PUBLIC_DEMO (Phase 3, step 2): scripted agents only, no probe, at most 3 lab
blocks, no technical error detail, all enforced by the backend before any agent
is built or any request leaves the server. Off, nothing changes.

Every test runs offline: a guard fails on any non-loopback connection or DNS
lookup, and the "endpoints" are in-process stubs on 127.0.0.1 that record what
reaches them."""
import json
import socket
import threading
from functools import partial
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dashboard import demo, server
from dashboard import sandbox as S
from lab import adapters, evaluation as E
from src.agents.base import Agent
from src.agents.schema import TransportError
from src.experiments.config import APPROVED_SCIENTIFIC

SECRET = "d3" * 32
FAKE_KEYS = {"ANTHROPIC_API_KEY": "sk-ant-fake-demo-key-0001", "OPENAI_API_KEY": "sk-fake-demo-key-0002"}
TOKEN = "tok-demo-7c1e"
SCRIPTED = {"kind": "scripted"}
SANDBOX_REQ = {"agents": {"A": "scripted", "B": "scripted"}, "pool": {"apples": 10, "books": 8},
               "preferences": {"A": {"apples": 60, "books": 40}, "B": {"apples": 30, "books": 70}},
               "strategy": "baseline_v1", "first_mover": "A"}
LEAKS = ("RuntimeError", "Traceback", "boom", "C:\\", "/home/", "10.0.0.1", ".env", "LAB_SCENARIO_SECRET", "sk-")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Any connection or DNS lookup that is not loopback fails the test."""
    def check(host):
        if str(host) not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"network access attempted: {host}")
    for name in ("connect", "connect_ex"):
        real = getattr(socket.socket, name)
        monkeypatch.setattr(socket.socket, name,
                            lambda self, addr, _r=real: (check(addr[0]) if isinstance(addr, tuple) else None) or _r(self, addr))
    real_gai = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: check(host) or real_gai(host, *a, **k))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv(demo.ENV, raising=False)
    monkeypatch.setenv(E.SECRET_ENV, SECRET)
    for k, v in FAKE_KEYS.items():  # keys present: the demo must refuse providers anyway
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(E, "LAB_DIR", tmp_path / "sandbox_runs" / "lab")
    monkeypatch.setattr(E, "SCRIPTED_PACE_S", 0)
    monkeypatch.setattr(E, "_evals", {})
    monkeypatch.setattr(E, "_active", None)
    monkeypatch.setattr(S, "SANDBOX_DIR", tmp_path / "sandbox_runs")
    monkeypatch.setattr(S, "SCRIPTED_PACE_S", 0)
    monkeypatch.setattr(S, "_runs", {})
    monkeypatch.setattr(S, "_active", None)


@pytest.fixture
def demo_on(monkeypatch):
    monkeypatch.setenv(demo.ENV, "1")


@pytest.fixture
def tripwires(monkeypatch):
    """Records every step past validation: spec parsing, agent construction, the probe."""
    calls = []
    for mod, name in ((adapters, "validate_agent"), (adapters, "build_agent"), (adapters, "probe"), (S, "_agent")):
        real = getattr(mod, name)
        monkeypatch.setattr(mod, name, lambda *a, _n=name, _r=real, **k: calls.append(_n) or _r(*a, **k))
    return calls


class Stub:
    """An HTTP agent endpoint on 127.0.0.1 that records every request it gets."""

    def __init__(self):
        stub, self.requests = self, []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                stub.requests.append(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps({"action_type": "WALK_AWAY"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/agent"


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.srv.shutdown()
    s.srv.server_close()


@pytest.fixture
def port():
    srv = server.LabServer(("127.0.0.1", 0), partial(server.Handler, directory=str(server.STATIC)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _call(port, method, path, body=None):
    c = HTTPConnection("127.0.0.1", port, timeout=20)
    data = None if body is None else json.dumps(body).encode()
    c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
    r = c.getresponse()
    return r.status, r.read().decode()


def _lab(x=SCRIPTED, y=SCRIPTED, blocks=1):
    return {"agents": {"X": x, "Y": y}, "strategy": "baseline_v1", "scenario": {"blocks": blocks, "categories": 3}}


# ---------------------------------------------------------------- the flag

@pytest.mark.parametrize("value, on", [("1", True), ("true", True), ("TRUE", True), (" yes ", True),
                                       ("", False), ("0", False), ("false", False), ("no", False), ("on", False),
                                       ("2", False), ("enabled", False)])
def test_only_an_explicit_truthy_value_enables_the_demo(monkeypatch, value, on):
    monkeypatch.setenv(demo.ENV, value)
    assert demo.enabled() is on


def test_demo_is_off_by_default():
    assert demo.enabled() is False and demo.flags() == {"public_demo": False, "scripted_only": False}


# ---------------------------------------------------------------- off: unchanged

def test_off_keeps_every_agent_kind_and_the_local_limits(stub, port):
    for kind in ("claude", "openai", "scripted"):
        assert S.validate({**SANDBOX_REQ, "agents": {"A": kind, "B": kind}})["agents"]["A"] == kind
    http = {"kind": "http", "url": stub.url, "label": "local", "token": TOKEN}
    cfg, tokens = E.validate(_lab(http, {"kind": "claude"}, blocks=E.MAX_BLOCKS))
    assert cfg["agents"]["X"]["kind"] == "http" and tokens["X"] == TOKEN and cfg["scenario"]["blocks"] == 10
    assert E.max_blocks() == E.MAX_BLOCKS == 10
    status, body = _call(port, "POST", "/api/lab/probe", {"agent": {"kind": "http", "url": stub.url, "label": "p"}})
    assert status == 200 and json.loads(body)["ok"] and len(stub.requests) == 1  # the probe still works locally


def test_off_keeps_technical_error_detail(monkeypatch):
    monkeypatch.setattr(adapters, "build_agent", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    ev = E.get(E.start(_lab(), background=False).id)
    assert ev["error"]["detail"] == "RuntimeError"


# ---------------------------------------------------------------- sandbox

def test_demo_sandbox_runs_scripted_agents(demo_on):
    run = S.start(SANDBOX_REQ, background=False)
    assert S.get(run.id)["status"] == "finished"


@pytest.mark.parametrize("pair", [("claude", "claude"), ("openai", "openai"), ("scripted", "claude"),
                                  ("openai", "scripted"), ("gemini", "gemini"), (None, None), ("http", "http")])
def test_demo_sandbox_refuses_everything_else_before_any_agent_exists(demo_on, tripwires, port, pair):
    status, body = _call(port, "POST", "/api/sandbox/runs", {**SANDBOX_REQ, "agents": {"A": pair[0], "B": pair[1]}})
    assert (status, json.loads(body)) == (400, {"error": demo.SCRIPTED_ONLY})
    assert tripwires == [] and S._runs == {} and not S.SANDBOX_DIR.exists()
    assert "API_KEY" not in body  # refused before the key check, which would name the key


# ---------------------------------------------------------------- Agent Lab

@pytest.mark.parametrize("blocks", [1, E.DEMO_MAX_BLOCKS])
def test_demo_lab_runs_scripted_pairs_up_to_the_cap(demo_on, blocks):
    ev = E.get(E.start(_lab(blocks=blocks), background=False).id)
    assert ev["status"] == "finished" and len(ev["trials"]) == 4 * blocks and ev["summary"]["used"] == 4 * blocks


def _bad_specs(stub):
    return [{"kind": "claude"}, {"kind": "openai"}, {"kind": "http", "url": stub.url, "label": "x", "token": TOKEN},
            {"kind": "gemini"}, {"kind": "ollama"}, {"kind": "SCRIPTED"}, "scripted", None, {}]


@pytest.mark.parametrize("seat", ["X", "Y"])
def test_demo_lab_refuses_every_other_agent_before_parsing_or_building(demo_on, tripwires, stub, port, seat):
    for bad in _bad_specs(stub):
        req = _lab(**{seat.lower(): bad})
        status, body = _call(port, "POST", "/api/lab/evaluations", req)
        assert (status, json.loads(body)) == (400, {"error": demo.SCRIPTED_ONLY}), bad
        assert stub.url not in body and TOKEN not in body
    assert tripwires == [] and stub.requests == [] and E._evals == {} and not E.LAB_DIR.exists()


def test_demo_lab_rejects_more_than_three_blocks(demo_on, port):
    status, body = _call(port, "POST", "/api/lab/evaluations", _lab(blocks=E.DEMO_MAX_BLOCKS + 1))
    assert status == 400 and "from 1 to 3" in json.loads(body)["error"]
    assert E._evals == {} and not E.LAB_DIR.exists()  # refused, not silently truncated
    with pytest.raises(adapters.SpecError, match="from 1 to 3"):
        E.validate(_lab(blocks=E.MAX_BLOCKS))


# ---------------------------------------------------------------- probe

def test_demo_has_no_probe_and_sends_nothing(demo_on, tripwires, stub, port):
    spec = {"kind": "http", "url": stub.url, "label": "p", "token": TOKEN}
    status, body = _call(port, "POST", "/api/lab/probe", {"agent": spec})
    assert (status, body) == _call(port, "POST", "/api/no/such/route", {"agent": spec})  # indistinguishable
    assert status == 405 and stub.requests == [] and tripwires == []


def test_demo_probe_is_absent_for_a_fully_valid_public_browser_request(demo_on, stub):
    handler = type("H", (server.Handler,), {"public_hosts": frozenset({"demo.example.org"})})
    srv = server.LabServer(("127.0.0.1", 0), partial(handler, directory=str(server.STATIC)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("POST", "/api/lab/probe", body=json.dumps({"agent": {"kind": "http", "url": stub.url, "label": "p"}}),
                  headers={"Host": "demo.example.org", "Origin": "https://demo.example.org",
                           "Content-Type": "application/json"})
        assert c.getresponse().status == 405 and stub.requests == []
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- error sanitization

class _Down(Agent):
    """Fails like an unreachable service whose error names an internal address."""
    name = "scripted"

    async def generate_response(self, view):
        raise TransportError("connection refused by http://10.0.0.1:8080/internal (C:\\srv\\app)")


def _no_leaks(text, config_error=False):
    """A report may name the secret's variable in its provenance formula, never its value;
    a configuration error must not even say how the server is set up."""
    for s in (*LEAKS, SECRET) if config_error else (*(x for x in LEAKS if x != "LAB_SCENARIO_SECRET"), SECRET):
        assert s not in text, s


def test_demo_lab_errors_carry_no_technical_detail(demo_on, monkeypatch):
    monkeypatch.setattr(adapters, "build_agent", lambda *a: (_ for _ in ()).throw(RuntimeError("boom C:\\x /home/y")))
    ev = E.get(E.start(_lab(), background=False).id)
    assert ev["status"] == "stopped" and "detail" not in ev["error"] and ev["error"]["title"] == "Unexpected error"
    _no_leaks(json.dumps(ev))


def test_demo_lab_aborts_carry_no_provider_detail(demo_on, monkeypatch):
    monkeypatch.setattr(adapters, "build_agent", lambda *a: _Down())
    ev = E.get(E.start(_lab(), background=False).id)
    aborted = [t for t in ev["trials"] if t["status"] == "aborted"]
    assert aborted and all("detail" not in t["abort"] for t in aborted)
    assert {t["abort"]["class"] for t in aborted} == {"provider_failure"}  # still classified, still no outcome
    _no_leaks(json.dumps(ev))


def test_demo_sandbox_errors_carry_no_technical_detail(demo_on, monkeypatch):
    monkeypatch.setattr(S, "_agent", lambda *a: (_ for _ in ()).throw(RuntimeError("boom C:\\x /home/y")))
    run = S.start(SANDBOX_REQ, background=False)
    v = S.get(run.id)
    assert v["status"] == "stopped" and "detail" not in v["error"]
    _no_leaks(json.dumps(v))


def test_demo_hides_how_the_server_is_configured(demo_on, monkeypatch, port):
    monkeypatch.delenv(E.SECRET_ENV)
    info = json.loads(_call(port, "GET", "/api/lab/info")[1])
    assert info["ready"] is False and info["error"] == E.DEMO_UNAVAILABLE
    status, body = _call(port, "POST", "/api/lab/evaluations", _lab())
    assert status == 400 and json.loads(body)["error"] == E.DEMO_UNAVAILABLE
    for text in (json.dumps(info), body):
        _no_leaks(text, config_error=True)


# ---------------------------------------------------------------- info endpoints

def test_lab_info_off(port):
    lab = json.loads(_call(port, "GET", "/api/lab/info")[1])
    assert (lab["public_demo"], lab["scripted_only"], lab["max_blocks"]) == (False, False, 10) and "max_lab_blocks" not in lab


def test_lab_info_on(demo_on, port):
    body = _call(port, "GET", "/api/lab/info")[1]
    lab = json.loads(body)
    assert (lab["public_demo"], lab["scripted_only"], lab["max_blocks"], lab["max_lab_blocks"]) == (True, True, 3, 3)
    assert lab["ready"] is True and lab["error"] is None
    for secret in (*FAKE_KEYS.values(), SECRET):
        assert secret not in body


SANDBOX_LIMITS = {"max_rounds": 10, "points": S.POINTS, "strategies": ["baseline_v1", "structured_v1"],
                  "limits": {"min_resources": S.MIN_CATS, "max_resources": S.MAX_CATS, "max_quantity": S.MAX_QTY}}
PROVIDER_TRACES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "key_name", "Anthropic", "OpenAI", "Claude", "GPT",
                   APPROVED_SCIENTIFIC["ClaudeAgent"]["model"], APPROVED_SCIENTIFIC["OpenAIAgent"]["model"])


def test_sandbox_info_off_keeps_provider_metadata_and_readiness(port, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    sbx = json.loads(_call(port, "GET", "/api/sandbox/info")[1])
    assert set(sbx) == {"public_demo", "scripted_only", "models", "max_rounds", "max_cost_usd", "points", "limits",
                        "strategies", "busy"}
    assert (sbx["public_demo"], sbx["scripted_only"]) == (False, False)
    assert {m["id"]: (m["provider"], m["model"], m["key_name"], m["ready"]) for m in sbx["models"]} == {
        "claude": ("Anthropic", APPROVED_SCIENTIFIC["ClaudeAgent"]["model"], "ANTHROPIC_API_KEY", True),
        "openai": ("OpenAI", APPROVED_SCIENTIFIC["OpenAIAgent"]["model"], "OPENAI_API_KEY", False)}
    assert {k: sbx[k] for k in SANDBOX_LIMITS} == SANDBOX_LIMITS


def test_sandbox_info_on_names_no_provider_at_all(demo_on, port):
    status, body = _call(port, "GET", "/api/sandbox/info")
    sbx = json.loads(body)
    assert status == 200 and (sbx["public_demo"], sbx["scripted_only"]) == (True, True)
    assert sbx["models"] == []
    for trace in (*PROVIDER_TRACES, *FAKE_KEYS.values(), SECRET):
        assert trace not in body, trace
    assert {k: sbx[k] for k in SANDBOX_LIMITS} == SANDBOX_LIMITS and sbx["busy"] is False  # the scripted demo's limits


# ---------------------------------------------------------------- the whole public flow, as a visitor's browser drives it

def _poll(port, path, done, limit=30.0):
    import time
    end = time.monotonic() + limit
    while time.monotonic() < end:
        status, body = _call(port, "GET", path)
        assert status == 200
        view = json.loads(body)
        if done(view):
            return view
        time.sleep(0.05)
    raise AssertionError(f"{path} did not finish")


def test_public_flow_empty_state_then_real_results_only(demo_on, port):
    """Setup -> run -> live -> completed -> report, through the HTTP API the page uses.
    Nothing is shown before a run exists, and everything shown after comes from that run."""
    assert json.loads(_call(port, "GET", "/api/sandbox/runs")[1]) == []        # empty state: no fabricated runs
    assert json.loads(_call(port, "GET", "/api/lab/evaluations")[1]) == []

    status, body = _call(port, "POST", "/api/sandbox/runs", SANDBOX_REQ)
    assert status == 202
    rid = json.loads(body)["id"]
    run = _poll(port, f"/api/sandbox/runs/{rid}", lambda v: v["status"] != "running")
    r = run["result"]
    assert run["status"] == "finished" and run["labels"] == {"A": "Scripted test agent A", "B": "Scripted test agent B"}
    assert r["outcome"] in ("agreed", "walked_away", "timeout", "invalid_action") and r["events"]
    assert len(run["turns"]) == len([e for e in r["events"] if e["action"] != "INVALID"])  # the live turns are the report's turns
    if r["outcome"] == "agreed":  # the reported split is the offer that was accepted, from this run's own transcript
        accepted = [e for e in r["events"] if e["action"] == "OFFER"][-1]["allocation"]
        assert r["final_allocation"] == accepted
    assert [x["id"] for x in json.loads(_call(port, "GET", "/api/sandbox/runs")[1])] == [rid]

    status, body = _call(port, "POST", "/api/lab/evaluations", _lab(blocks=1))
    assert status == 202
    eid = json.loads(body)["id"]
    ev = _poll(port, f"/api/lab/evaluations/{eid}", lambda v: v["status"] != "running" and not v.get("sealed"))
    assert ev["status"] == "finished" and len(ev["trials"]) == 4 and ev["summary"]["used"] == 4
    assert all(t["events"] for t in ev["trials"])  # every negotiation's transcript is in the report
    assert {a["kind"] for a in ev["agents"].values()} == {"scripted"}
    for text in (json.dumps(run), json.dumps(ev)):
        for leak in (*FAKE_KEYS.values(), "API_KEY", SECRET, "http://", "https://"):
            assert leak not in text, leak
