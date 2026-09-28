"""Agent Lab (Phase 2): adapters, information boundary, security, design, failures, metrics.

Every HTTP agent here is an in-process stub on 127.0.0.1; no test reaches an
external network or a model provider. Numbers in comments refer to the
regression list in docs/phase2-agent-lab-plan.md §12.
"""
import asyncio
import hashlib
import json
import random
import socket
import statistics
import threading
import time
import urllib.request
from functools import partial
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dashboard import data as D
from dashboard import server
from lab import adapters, evaluation as E, metrics as LM
from lab.example_http_agent import decide
from src.agents.base import Agent, AgentView, TranscriptTurn
from src.agents.mock_agent import MockAgent
from src.agents.prompting import build_prompt, system_instructions
from src.agents.schema import InvalidAgentOutputError, NegotiationAction, TransportError
from src.environment.optimum import compute_optimal_welfare
from src.environment.resources import generate_resource_pool
from src.environment.valuations import generate_valuation
from src.experiments import provenance as P
from src.negotiation.protocol import EvaluatorState, NegotiationAborted, NegotiationRecord, NegotiationSession

SECRET = "5ec7e7" + "a1" * 29  # 64 hex characters = 32 bytes
KEY = bytes.fromhex(SECRET)
TOKEN = "sk-test-SECRET-7f3a9c"


# ---------------------------------------------------------------- stub endpoints

class Stub:
    """An HTTP agent on an ephemeral port. `reply(obs)` returns a dict (sent as
    JSON), bytes (sent raw), or (status, payload). Every request is recorded."""

    def __init__(self, reply, delay=0.0):
        stub = self
        self.reply, self.delay, self.requests = reply, delay, []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                obs = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"obs": obs, "headers": dict(self.headers)})
                time.sleep(stub.delay)
                out = stub.reply(obs)
                status, out = out if isinstance(out, tuple) else (200, out)
                body = out if isinstance(out, bytes) else json.dumps(out).encode()
                self.send_response(status)
                if 300 <= status < 400:
                    self.send_header("Location", getattr(stub, "location", "http://127.0.0.1:9/elsewhere"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/agent"

    def spec(self, label="stub", **kw):
        return {"kind": "http", "url": self.url, "label": label, **kw}

    def close(self):
        self.srv.shutdown()


class RawServer:
    """A TCP server whose per-connection behaviour is a plain function: for
    endpoints that misbehave below HTTP (slow drip, silence)."""

    def __init__(self, handle):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/"
        self._stop = False

        def loop():
            while not self._stop:
                try:
                    conn, _ = self.sock.accept()
                except OSError:
                    return
                threading.Thread(target=self._run, args=(handle, conn), daemon=True).start()
        threading.Thread(target=loop, daemon=True).start()

    @staticmethod
    def _run(handle, conn):
        try:
            conn.recv(65536)
            handle(conn)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self._stop = True
        self.sock.close()


@pytest.fixture
def stubs():
    made = []
    yield lambda reply, delay=0.0: made.append(Stub(reply, delay)) or made[-1]
    for s in made:
        s.close()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(E.SECRET_ENV, SECRET)
    monkeypatch.setattr(E, "LAB_DIR", tmp_path / "sandbox_runs" / "lab")
    monkeypatch.setattr(E, "SCRIPTED_PACE_S", 0)
    monkeypatch.setattr(E, "_evals", {})
    monkeypatch.setattr(E, "_active", None)


def _state(seed=11, first="A", max_rounds=10):
    pool = generate_resource_pool(seed=seed)
    v = {r: generate_valuation(seed, r, pool) for r in "AB"}
    alloc, opt = compute_optimal_welfare(pool, v["A"], v["B"])
    return EvaluatorState(instance_seed=seed, resource_pool=pool, valuation_A=v["A"], valuation_B=v["B"],
                          optimal_allocation=alloc, optimal_welfare=opt, max_rounds=max_rounds, first_mover=first,
                          method="baseline_v1", model_A_name="a", model_B_name="b")


def _http(stub_or_url, **kw):
    url = stub_or_url if isinstance(stub_or_url, str) else stub_or_url.url
    return adapters.HTTPAgent(url, "stub", "baseline_v1", **kw)


def _run(a, b, state=None):
    return asyncio.run(NegotiationSession(a, b, state or _state()).run())


def _mine(share):
    return lambda obs: {"action_type": "OFFER", "message": "x",
                        "allocation": {c: round(q * share) for c, q in obs["resource_pool"].items()}}


def _req(x, y, blocks=1, k=3, strategy="baseline_v1"):
    return {"agents": {"X": x, "Y": y}, "strategy": strategy, "scenario": {"blocks": blocks, "categories": k}}


SCRIPTED = {"kind": "scripted"}


def _evaluate(x=SCRIPTED, y=SCRIPTED, **kw):
    return E.get(E.start(_req(x, y, **kw), background=False).id)


def _serve(store=None):
    server.Handler.store = store or type("S", (), {"snapshot": lambda self: {"research": True},
                                                   "negotiation": lambda self, key: None})()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(server.Handler, directory=str(server.STATIC)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(port, path, host=None):
    c = HTTPConnection("127.0.0.1", port)
    c.request("GET", path, headers={"Host": host} if host else {})
    r = c.getresponse()
    return r.status, r.read().decode()


def _post(port, path, body, headers=None):
    c = HTTPConnection("127.0.0.1", port)
    c.request("POST", path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
    r = c.getresponse()
    return r.status, r.read().decode()


def _wait(eid, limit=20.0):
    end = time.monotonic() + limit
    while E.sealed() and time.monotonic() < end:
        time.sleep(0.05)
    return E.get(eid)


# ---------------------------------------------------------------- adapters

def test_valid_offer_becomes_a_two_sided_action(stubs):
    s = stubs(_mine(0.6))
    action = asyncio.run(_http(s).generate_response(adapters.PROBE_VIEW))
    assert action.action_type == "OFFER"
    assert action.allocation == {"A": {"widgets": 6, "gadgets": 6}, "B": {"widgets": 4, "gadgets": 4}}


@pytest.mark.parametrize("reply, reason", [
    (lambda o: b"not json", "malformed_output"),
    (lambda o: {"action_type": "HAGGLE"}, "malformed_output"),
    (lambda o: {"action_type": "OFFER", "allocation": {"widgets": 1.5}}, "invalid_allocation"),
    (lambda o: {"action_type": "OFFER", "allocation": None, "message": "m" * 1001}, "malformed_output"),
    (lambda o: b"{" + b" " * (65 * 1024) + b"}", "malformed_output"),                     # over the 64 KiB cap
    (lambda o: b"[" * 60_000, "malformed_output"),                                          # 14: RecursionError
    (lambda o: b'{"action_type": "OFFER", "allocation": {"widgets": NaN, "gadgets": 1}}', "invalid_allocation"),  # 15
    (lambda o: b"\xff\xfe{\"action_type\": \"ACCEPT\"}", "malformed_output"),               # 15: not UTF-8
    (lambda o: b'"ACCEPT"', "malformed_output"),                                            # 15: wrong JSON types
    (lambda o: b"[1, 2]", "malformed_output"),
    (lambda o: b"null", "malformed_output"),
    (lambda o: b'{"action_type": "OFFER", "allocation": {"widgets": ' + b"9" * 5000 + b"}}", "malformed_output"),  # 15
])
def test_every_unreadable_reply_is_a_frozen_invalid_reason(stubs, reply, reason):
    s = stubs(reply)
    with pytest.raises(InvalidAgentOutputError) as exc:
        asyncio.run(_http(s).generate_response(adapters.PROBE_VIEW))
    assert exc.value.reason == reason


def test_huge_but_parseable_quantity_is_rejected_by_the_engine(stubs):
    s = stubs(lambda o: {"action_type": "OFFER", "allocation": {c: 10 ** 30 for c in o["resource_pool"]}})
    r = _run(_http(s), MockAgent())
    assert (r.outcome, r.invalid_reason) == ("invalid_action", "invalid_allocation")


def test_nested_json_ends_one_negotiation_not_the_evaluation(stubs):  # 14
    deep = stubs(lambda o: b"[" * 60_000)
    ev = _evaluate(deep.spec("deep"))
    assert ev["status"] == "finished" and len(ev["trials"]) == 4
    assert {(t["outcome"], t["invalid_reason"]) for t in ev["trials"]} == {("invalid_action", "malformed_output")}
    out = asyncio.run(adapters.probe(adapters.validate_agent(deep.spec(), "X")[0], None))
    assert not out["ok"] and out["title"] == "Reply rejected"


@pytest.mark.parametrize("status", [500, 401])
def test_non_2xx_is_a_transport_error(stubs, status):
    s = stubs(lambda o: (status, b""))
    with pytest.raises(TransportError, match=f"HTTP {status}"):
        asyncio.run(_http(s).generate_response(adapters.PROBE_VIEW))


@pytest.mark.parametrize("status", [301, 302, 307])
def test_redirects_are_refused_and_never_followed(stubs, status):  # 20
    target = stubs(_mine(0.5))
    s = stubs(lambda o: (status, b""))
    s.location = target.url
    with pytest.raises(TransportError, match=f"HTTP {status}"):
        asyncio.run(_http(s).generate_response(adapters.PROBE_VIEW))
    assert target.requests == []


def test_unreachable_endpoint_is_a_transport_error():
    with pytest.raises(TransportError):  # refused, or (Windows retries SYNs) out of time: both are transport
        asyncio.run(_http("http://127.0.0.1:9/", timeout_s=2).generate_response(adapters.PROBE_VIEW))


def _timed(coro_fn):
    start = time.monotonic()
    try:
        coro_fn()
    except (TransportError, NegotiationAborted) as exc:
        return time.monotonic() - start, exc
    raise AssertionError("expected a timeout")


def _drip_body(conn):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
    for _ in range(60):
        time.sleep(0.3)
        conn.sendall(b" ")


def _drip_headers(conn):
    for ch in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 60:
        time.sleep(0.3)
        conn.sendall(bytes([ch]))


def _silent(conn):
    time.sleep(6)


@pytest.mark.parametrize("behaviour", [_drip_body, _drip_headers, _silent])
def test_whole_turn_deadline_holds_against_slow_endpoints(behaviour):  # 16, 17, 18
    srv = RawServer(behaviour)
    try:
        took, exc = _timed(lambda: asyncio.run(_http(srv.url, timeout_s=1).generate_response(adapters.PROBE_VIEW)))
        assert took < 1.6 and "no reply within 1 s" in str(exc)
        # The whole negotiation (asyncio.run included) ends on time too: the audit measured 12 s here.
        took, exc = _timed(lambda: _run(_http(srv.url, timeout_s=1), MockAgent()))
        assert took < 1.8 and isinstance(exc, NegotiationAborted) and exc.reason == "transport_failure"
    finally:
        srv.close()


def test_deadline_holds_when_every_worker_is_stuck():  # 19
    srv = RawServer(_silent)
    try:
        stuck = [threading.Thread(target=lambda: _timed(lambda: asyncio.run(
            _http(srv.url, timeout_s=3).generate_response(adapters.PROBE_VIEW)))) for _ in range(10)]
        for t in stuck:
            t.start()
        time.sleep(0.3)
        took, _ = _timed(lambda: asyncio.run(_http(srv.url, timeout_s=1).generate_response(adapters.PROBE_VIEW)))
        assert took < 1.7
        for t in stuck:
            t.join()
    finally:
        srv.close()


def test_engine_outcomes_through_the_http_adapter(stubs):
    over = stubs(lambda o: {"action_type": "OFFER", "allocation": {c: q + 1 for c, q in o["resource_pool"].items()}})
    r = _run(_http(over), MockAgent())
    assert (r.outcome, r.invalid_reason, r.num_rounds) == ("invalid_action", "invalid_allocation", 1)
    acc = stubs(lambda o: {"action_type": "ACCEPT"})
    r = _run(_http(acc), MockAgent())
    assert (r.outcome, r.invalid_reason) == ("invalid_action", "illegal_accept")
    walk = stubs(lambda o: {"action_type": "WALK_AWAY", "message": "bye"})
    r = _run(MockAgent(), _http(walk))
    assert (r.outcome, r.num_rounds, r.transcript[-1].actor) == ("walked_away", 2, "B")


def test_final_round_offer_is_a_timeout_and_is_flagged_in_the_observation(stubs):
    greedy = stubs(_mine(1.0))
    r = _run(MockAgent(accept_threshold=1.01), _http(greedy))  # seat B: rounds 2, 4, ..., 10
    assert (r.outcome, r.num_rounds) == ("timeout", 10)
    last = greedy.requests[-1]["obs"]
    assert last["is_final_round"] and last["round"] == 10 and "OFFER" not in last["allowed_actions"]


def test_http_calls_are_logged_without_usage_and_do_not_trip_the_cost_cap(stubs):
    s = stubs(_mine(0.6))
    budget = E._ProviderBudget(max_cost_usd=0.01, prices=E.APPROVED_PRICES)
    r = asyncio.run(NegotiationSession(_http(s), MockAgent(), _state(), budget=budget).run())
    assert r.calls and all(c["model"] == "http:stub" and c["input_tokens"] is None for c in r.calls)
    assert budget.exceeded() is None


# ---------------------------------------------------------------- privacy: the observation

OBS_KEYS = {"contract", "role", "resource_pool", "own_valuation", "round", "max_rounds", "rounds_remaining",
            "is_final_round", "allowed_actions", "standing_offer", "transcript", "instructions", "prompt"}


def _contains(obj, target) -> bool:
    if obj == target:
        return True
    if isinstance(obj, dict):
        return any(_contains(v, target) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains(v, target) for v in obj)
    return False


def test_each_seat_receives_only_its_own_observation(stubs):
    a, b = stubs(_mine(0.7)), stubs(_mine(0.7))
    st = _state(seed=5)
    _run(_http(a), _http(b), st)
    opt = {"A": st.optimal_allocation.a_A, "B": st.optimal_allocation.a_B}
    for stub, own, other in ((a, st.valuation_A, st.valuation_B), (b, st.valuation_B, st.valuation_A)):
        assert stub.requests
        for req in stub.requests:
            obs = req["obs"]
            assert set(obs) == OBS_KEYS and obs["own_valuation"] == own
            assert not _contains(obs, other) and not _contains(obs, opt["A"]) and not _contains(obs, opt["B"])


def test_prompts_in_the_observation_are_the_phase1_text(stubs):
    s = stubs(_mine(0.6))
    asyncio.run(adapters.HTTPAgent(s.url, "p", "structured_v1").generate_response(adapters.PROBE_VIEW))
    obs = s.requests[0]["obs"]
    assert obs["instructions"] == system_instructions(adapters.PROBE_VIEW, "structured_v1")
    assert obs["prompt"] == build_prompt(adapters.PROBE_VIEW)


# ---------------------------------------------------------------- C1: secret-keyed scenarios

def test_seed_recovery_from_an_observation_fails():  # 1 (the audit recovered it in 0.02 s)
    sc = E.scenario(KEY, "a" * 32, 0, "c1", 3)
    pool, own = sc["pool"], sc["valuation"]["A"]
    names = E.CATEGORY_NAMES[:3]
    for cand in list(range(0, 200_000)) + list(range(30_000, 30_010)) + list(range(40_000, 40_060)):
        rng = random.Random(cand)
        if all(rng.randint(10, 50) == pool[n] for n in names):
            assert generate_valuation(cand, "A", pool) != own, f"seed {cand} reproduces the hidden scenario"


def test_scenarios_depend_on_key_set_block_and_cell():  # 2
    same = E.scenario(KEY, "s" * 32, 3, "c2", 3)
    assert same == E.scenario(KEY, "s" * 32, 3, "c2", 3)
    for other in (E.scenario(bytes(32), "s" * 32, 3, "c2", 3), E.scenario(KEY, "t" * 32, 3, "c2", 3),
                  E.scenario(KEY, "s" * 32, 4, "c2", 3), E.scenario(KEY, "s" * 32, 3, "c3", 3)):
        assert other["fingerprint"] != same["fingerprint"]


def test_scenarios_come_from_the_unchanged_phase1_generators():  # 5
    seed = E._derive(KEY, "s" * 32, "scenario", 0, "c1")
    sc = E.scenario(KEY, "s" * 32, 0, "c1", 3)
    assert sc["pool"] == generate_resource_pool(seed=seed) == generate_resource_pool(
        seed=seed, num_categories=3, category_names=E.CATEGORY_NAMES[:3])
    assert sc["valuation"] == {r: generate_valuation(seed, r, sc["pool"]) for r in "AB"}
    assert E.scenario(KEY, "s" * 32, 0, "c1", 5)["pool"].keys() == {"widgets", "gadgets", "components", "gizmos", "parts"}


@pytest.mark.parametrize("value", [None, "", "abc", "zz" * 40, "ab" * 31])  # missing, non-hex, too short
def test_scenario_secret_is_mandatory(monkeypatch, value):  # 4
    if value is None:
        monkeypatch.delenv(E.SECRET_ENV, raising=False)
    else:
        monkeypatch.setenv(E.SECRET_ENV, value)
    with pytest.raises(adapters.SpecError, match=E.SECRET_ENV):
        E.start(_req(SCRIPTED, SCRIPTED), background=False)
    assert E.ready() == E.SECRET_MISSING and not E.LAB_DIR.exists() and not E._evals


def test_missing_secret_is_explained_by_the_server(monkeypatch):  # 4
    monkeypatch.delenv(E.SECRET_ENV, raising=False)
    srv = _serve()
    port = srv.server_address[1]
    try:
        info = json.loads(_get(port, "/api/lab/info")[1])
        assert info["ready"] is False and "persistent scenario secret" in info["error"]
        status, body = _post(port, "/api/lab/evaluations", json.dumps(_req(SCRIPTED, SCRIPTED)))
        assert status == 400 and E.SECRET_ENV in json.loads(body)["error"]
    finally:
        srv.shutdown()


def test_key_id_is_stable_and_one_way():  # 4
    assert E.key_id(KEY) == E.key_id(bytes.fromhex(SECRET)) and len(E.key_id(KEY)) == 12
    assert E.key_id(KEY) not in SECRET and E.key_id(KEY) != E.key_id(bytes(32))


def test_secret_and_derived_seeds_never_leave_the_server(stubs, capsys):  # 3
    ok, dead = stubs(_mine(0.6)), stubs(lambda o: (500, b""))
    srv = _serve()
    port = srv.server_address[1]
    try:
        texts, seeds = [], []
        for x in (ok.spec("ok", token=TOKEN), dead.spec("dead")):
            status, body = _post(port, "/api/lab/evaluations", json.dumps(_req(x, SCRIPTED, blocks=2)))
            assert status == 202
            eid = json.loads(body)["id"]
            texts.append(json.dumps(_wait(eid)))
            ev = E._evals[eid]
            seeds += [E._derive(KEY, ev.set_id, "scenario", t["block"], t["cell"]) for t in ev.planned]
            texts += [_get(port, f"/api/lab/evaluations/{eid}")[1], _get(port, "/api/lab/evaluations")[1],
                      (E.LAB_DIR / f"{eid}.json").read_text(encoding="utf-8"), repr(ev.__dict__)]
        texts += [json.dumps(r["obs"]) for s in (ok, dead) for r in s.requests]  # what the agents received
        out = capsys.readouterr()
        texts += [out.out, out.err]
        blob = "\n".join(texts)
        assert SECRET not in blob
        assert SECRET.upper() not in blob
        assert TOKEN not in blob  # the token reaches only its own endpoint's headers (tested separately)
        for seed in seeds:
            assert str(seed) not in blob and f"{seed:x}" not in blob
        assert len(seeds) == 16 and '"aborted"' in blob and "agent_failure" in blob  # error records were produced
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- C3: the block design

def test_block_matrix_is_exact_and_every_scenario_is_distinct():  # 6
    ev = _evaluate(blocks=3)
    trials = ev["trials"]
    assert len(trials) == 12 and [t["position"] for t in trials] == list(range(12))
    for b in range(3):
        cells = {t["cell"]: (t["seats"], t["first_mover"]) for t in trials if t["block"] == b}
        assert cells == {"c1": ({"A": "X", "B": "Y"}, "A"), "c2": ({"A": "X", "B": "Y"}, "B"),
                         "c3": ({"A": "Y", "B": "X"}, "A"), "c4": ({"A": "Y", "B": "X"}, "B")}
    assert len({t["fingerprint"] for t in trials}) == 12


def test_stateful_agents_never_learn_an_opponent_valuation(stubs):  # 7: the audit's seat-swap exploit
    learned = []

    def stateful():  # one private memory per agent, keyed by the pool it was shown
        memory = {}

        def reply(obs):
            seen = memory.setdefault(json.dumps(obs["resource_pool"], sort_keys=True), {})
            other = "B" if obs["role"] == "A" else "A"
            if other in seen:
                learned.append(seen[other])  # it once held the valuation its opponent holds now
            seen[obs["role"]] = obs["own_valuation"]
            return decide(obs)
        return reply

    x, y = stubs(stateful()), stubs(stateful())
    ev = _evaluate(x.spec("x"), y.spec("y"), blocks=3)
    assert ev["summary"]["used"] == 12 and learned == []
    for stub in (x, y):  # no agent ever sees one scenario twice
        firsts = {(json.dumps(r["obs"]["resource_pool"], sort_keys=True), json.dumps(r["obs"]["own_valuation"]))
                  for r in stub.requests}
        assert len(firsts) == 12


def test_cell_order_is_a_keyed_permutation():  # 8
    a, again, b = E.plan(KEY, "s" * 32, 20), E.plan(KEY, "s" * 32, 20), E.plan(KEY, "t" * 32, 20)
    order = lambda p: [t["cell"] for t in p]  # noqa: E731
    assert order(a) == order(again) and order(a) != order(b)
    assert order(a) != ["c1", "c2", "c3", "c4"] * 20
    for blk in range(20):
        assert sorted(t["cell"] for t in a if t["block"] == blk) == ["c1", "c2", "c3", "c4"]


def test_cells_draw_from_one_scenario_distribution():  # 9
    per_cell = {c: [E.scenario(KEY, "d" * 32, b, c, 3) for b in range(200)] for c in E.CELLS}
    wstar = {c: statistics.fmean(s["optimal_welfare"] for s in v) for c, v in per_cell.items()}
    units = {c: statistics.fmean(sum(s["pool"].values()) for s in v) for c, v in per_cell.items()}
    overall_w, overall_u = statistics.fmean(wstar.values()), statistics.fmean(units.values())
    assert all(abs(w - overall_w) < 8 for w in wstar.values())
    assert all(abs(u - overall_u) < 6 for u in units.values())


# ---------------------------------------------------------------- C2: sealed API, Host pinning

FORBIDDEN_WHILE_SEALED = ("config", "url", "set_id", "key_id", "fingerprint", "resource_pool", "valuation",
                          "utility", "events", "summary", "provenance", "optimal", "per_agent", "final_allocation")


def test_mid_run_spy_learns_nothing_about_any_evaluation(stubs):  # 10
    srv = _serve()
    lab = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        prior = E.start(_req(SCRIPTED, SCRIPTED), background=False).id
        seen, holder = [], {}

        def spy(obs):
            if "id" in holder:
                for path in ("/api/lab/evaluations", f"/api/lab/evaluations/{holder['id']}",
                             f"/api/lab/evaluations/{prior}"):
                    seen.append((path, urllib.request.urlopen(lab + path).read().decode()))
            return decide(obs)

        s = stubs(spy)
        holder["id"] = E.start(_req(s.spec("spy"), SCRIPTED)).id
        final = _wait(holder["id"])
        assert seen and final["status"] == "finished"
        for path, body in seen:
            view = json.loads(body)
            for key in FORBIDDEN_WHILE_SEALED:
                assert f'"{key}' not in body, (path, key)
            assert s.url not in body and "127.0.0.1" not in body
            if isinstance(view, dict):
                assert view["sealed"] is True
                for t in view.get("trials", []):
                    assert set(t) <= set(E.PUBLIC_TRIAL_FIELDS)
                if view["id"] == prior:
                    assert "trials" not in view  # another evaluation: header only
    finally:
        srv.shutdown()


def test_full_report_after_completion_and_listing_never_has_urls(stubs):  # 11, 13
    s = stubs(_mine(0.6))
    ev = _evaluate(s.spec("done"))
    assert ev["provenance"]["agents"]["X"]["url"] == s.url and ev["trials"][0]["fingerprint"]
    assert ev["trials"][0]["valuation_A"] and ev["summary"]["used"] == 4
    listing = json.dumps(E.listing())
    assert s.url not in listing and "127.0.0.1" not in listing and '"config"' not in listing


def test_api_gets_are_pinned_to_this_host():  # 12
    srv = _serve()
    port = srv.server_address[1]
    eid = E.start(_req(SCRIPTED, SCRIPTED), background=False).id
    try:
        paths = ["/api/lab/evaluations", f"/api/lab/evaluations/{eid}", "/api/lab/info", "/api/sandbox/info",
                 "/api/sandbox/runs", "/api/snapshot", "/api/negotiation?key=x"]
        for path in paths:
            assert _get(port, path, host="evil.example")[0] == 403, path
            assert _get(port, path, host=f"evil.example:{port}")[0] == 403, path
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            assert _get(port, f"/api/lab/evaluations/{eid}", host=host)[0] == 200
            assert _get(port, "/api/snapshot", host=host)[0] == 200
        assert _get(port, "/", host="evil.example")[0] == 200  # static app code stays public
    finally:
        srv.shutdown()


def test_post_guard_and_catch_all(stubs, monkeypatch):  # 14 (server side)
    s = stubs(_mine(0.6))
    srv = _serve()
    port = srv.server_address[1]
    try:
        req = json.dumps(_req(s.spec(token=TOKEN), SCRIPTED))
        assert _post(port, "/api/lab/evaluations", req, {"Origin": "http://evil.example"})[0] == 403
        assert _post(port, "/api/lab/evaluations", req, {"Host": f"evil.example:{port}",
                                                          "Origin": f"http://evil.example:{port}"})[0] == 403
        assert _post(port, "/api/lab/evaluations", "x" * 20_000)[0] == 413
        assert _post(port, "/api/lab/probe", "{nope")[0] == 400
        status, body = _post(port, "/api/lab/probe", json.dumps({"agent": s.spec(token=TOKEN)}))
        assert status == 200 and json.loads(body)["ok"] and TOKEN not in body

        def boom(req):
            raise RuntimeError("secret-looking internals")
        monkeypatch.setitem(server.POST_ROUTES, "/api/lab/probe", boom)
        status, body = _post(port, "/api/lab/probe", "{}")
        assert status == 500 and json.loads(body) == {"error": "internal error"}
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- security

def test_specs_are_data_not_code():
    for bad in ({"kind": "python", "code": "import os"}, {"kind": "module", "path": "os"}, {"kind": "gemini"},
                {"kind": "ollama"}, None, "scripted"):
        with pytest.raises(adapters.SpecError):
            adapters.validate_agent(bad, "Agent X")
    spec, _ = adapters.validate_agent({"kind": "http", "url": "http://127.0.0.1:1/", "label": "a",
                                       "code": "__import__('os')"}, "Agent X")
    assert set(spec) == {"kind", "url", "label", "timeout_s", "auth"}


@pytest.mark.parametrize("url", ["ftp://127.0.0.1/x", "file:///etc/passwd", "javascript:alert(1)",
                                 "http://user:pw@127.0.0.1/", "http:///nohost", "127.0.0.1:80",
                                 "http://127.0.0.1:99999/", "http://127.0.0.1:port/"])
def test_endpoint_urls_are_restricted(url):
    with pytest.raises(adapters.SpecError):
        adapters.validate_agent({"kind": "http", "url": url, "label": "a"}, "Agent X")


def test_token_is_sent_to_its_endpoint_only(stubs):
    s, other = stubs(_mine(0.7)), stubs(_mine(0.7))
    ev = _evaluate(s.spec(token=TOKEN), other.spec("other"))
    assert s.requests and all(r["headers"].get("Authorization") == f"Bearer {TOKEN}" for r in s.requests)
    assert all("Authorization" not in r["headers"] and TOKEN not in json.dumps(r["obs"]) for r in other.requests)
    assert TOKEN not in json.dumps(ev) and ev["agents"]["X"]["auth"] == "bearer token (session only, not stored)"


# ---------------------------------------------------------------- I3/I4: failures and complete blocks

def test_seat_selective_failure_yields_no_aggregate(stubs):  # 21: the audit's selective-failure attack
    picky = stubs(lambda o: (500, b"") if o["role"] == "B" else _mine(1.0)(o))
    ev = _evaluate(picky.spec("picky"), blocks=3)
    s = ev["summary"]
    assert ev["status"] == "stopped" and s["aborted"] == E.MAX_ABORTS
    assert all(t["abort"]["class"] == "agent_failure" and t["abort"]["attributed"] and t["abort"]["agent"] == "X"
               for t in ev["trials"] if t["status"] == "aborted")
    assert s["failures"]["X"]["agent_failures"] == 3 and s["failures"]["X"]["by_seat"] == {"A": 0, "B": 3}
    assert s["failures"]["Y"]["agent_failures"] == 0
    assert s["blocks"]["complete"] == [] and s["used"] == 0 and s["exclusion_warning"]
    assert s["outcomes"]["agreed"] is None and s["per_agent"]["X"]["utility"] == {"n": 0}


def test_one_failure_excludes_its_block_and_aggregates_use_complete_blocks_only(stubs):  # 22, 25
    calls = {"n": 0}

    def flaky(obs):
        calls["n"] += 1
        return (500, b"") if calls["n"] == 1 else decide(obs)

    ev = _evaluate(stubs(flaky).spec("flaky"), blocks=3)
    s, trials = ev["summary"], ev["trials"]
    failed = [t for t in trials if t["status"] == "aborted"]
    assert ev["status"] == "finished" and len(failed) == 1 and failed[0]["abort"]["class"] == "agent_failure"
    assert s["blocks"]["excluded"] == [{"block": failed[0]["block"], "reasons": ["agent_failure"]}]
    used = [t for t in trials if t["block"] != failed[0]["block"]]
    assert len(used) == 8 == s["used"] and len(s["blocks"]["complete"]) == 2
    assert s["outcomes"]["agreed"] == sum(t["outcome"] == "agreed" for t in used) / 8
    assert sum(s["outcomes"].values()) == pytest.approx(1.0)  # failures never enter outcome rates
    assert s["rwe"]["median"] == pytest.approx(statistics.median(t["rwe"] for t in used))
    assert s["per_agent"]["X"]["utility"]["median"] == pytest.approx(statistics.median(t["per_agent"]["X"]["utility"] for t in used))
    assert s["failures"]["X"] == {"attempted": 12, "agent_failures": 1, "rate": 1 / 12,
                                  "by_seat": {failed[0]["abort"]["seat"]: 1, ("B" if failed[0]["abort"]["seat"] == "A" else "A"): 0}}


class _DownProvider(Agent):
    """A provider-kind agent whose transport always fails (no network is touched)."""
    name = "claude:stub"

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        raise TransportError("provider unavailable")


@pytest.mark.parametrize("url", ["http://a..b/", f"http://{'a' * 64}.example/"])  # empty / overlong IDNA label
def test_idna_invalid_hostname_is_an_agent_failure(url):  # fails in IDNA encoding, before any network call
    ev = _evaluate(SCRIPTED, {"kind": "http", "url": url, "label": "badhost"})
    s = ev["summary"]
    assert ev["status"] == "stopped" and ev["error"]["title"] == "Agent Y did not answer"  # not "Unexpected error"
    assert len(ev["trials"]) == E.MAX_ABORTS
    for t in ev["trials"]:
        assert t["status"] == "aborted" and "outcome" not in t and "per_agent" not in t
        assert t["abort"]["class"] == "agent_failure" and t["abort"]["agent"] == "Y" and t["abort"]["attributed"]
        assert "UnicodeError" in t["abort"]["detail"]
    assert s["aborts_by_class"] == {"agent_failure": E.MAX_ABORTS, "provider_failure": 0, "budget_exhausted": 0}
    assert s["failures"]["Y"]["agent_failures"] == E.MAX_ABORTS and s["failures"]["X"]["agent_failures"] == 0
    assert s["used"] == 0 and s["outcomes"]["agreed"] is None


def test_provider_failure_is_not_attributed(monkeypatch):  # 23
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-used")
    real = adapters.build_agent
    monkeypatch.setattr(adapters, "build_agent", lambda spec, tok, strat: _DownProvider() if spec["kind"] == "claude" else real(spec, tok, strat))
    ev = _evaluate({"kind": "claude"}, blocks=2)
    s = ev["summary"]
    assert s["aborts_by_class"] == {"agent_failure": 0, "provider_failure": 3, "budget_exhausted": 0}
    assert all(not t["abort"]["attributed"] for t in ev["trials"])
    assert s["failures"]["X"]["agent_failures"] == 0 and s["used"] == 0


def test_budget_stop_leaves_blocks_not_run(monkeypatch):  # 24
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-used")
    monkeypatch.setattr(E, "MAX_COST_USD", 0.0)
    monkeypatch.setattr(adapters, "build_agent", lambda spec, tok, strat: MockAgent())
    ev = _evaluate({"kind": "claude"}, blocks=3)
    s = ev["summary"]
    assert ev["status"] == "stopped" and s["aborts_by_class"]["budget_exhausted"] == 1
    assert s["blocks"]["not_run"] == 2 and s["blocks"]["complete"] == [] and s["outcomes"]["agreed"] is None


def test_walk_aways_follow_the_agent_across_seats(stubs):  # 28 (exact counts)
    walker = stubs(lambda o: {"action_type": "WALK_AWAY"} if o["round"] > 1 else _mine(0.9)(o))
    ev = _evaluate(SCRIPTED, walker.spec("walker"))
    pa = ev["summary"]["per_agent"]
    assert (pa["Y"]["walk_aways"], pa["X"]["walk_aways"]) == (4, 0)
    for t in ev["trials"]:
        assert t["outcome"] == "walked_away" and t["per_agent"]["Y"]["seat"] == t["events"][-1]["actor"]


def test_request_validation():
    ok = _req(SCRIPTED, SCRIPTED)
    E.validate(ok)
    for bad in ({**ok, "strategy": "x"}, {**ok, "scenario": {"blocks": 11, "categories": 3}},
                {**ok, "scenario": {"blocks": True, "categories": 3}}, {**ok, "scenario": {"blocks": 1, "categories": 6}},
                {**ok, "scenario": {"base_seed": 5, "instances": 1}}, [ok]):
        with pytest.raises(adapters.SpecError):
            E.validate(bad)


def test_missing_provider_key_is_refused_before_anything_runs(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(adapters.SpecError, match="ANTHROPIC_API_KEY"):
        E.start(_req({"kind": "claude"}, SCRIPTED))
    assert not E.LAB_DIR.exists()


# ---------------------------------------------------------------- I5: provenance

def test_provenance_matches_phase1_helpers_and_fingerprints_recompute():  # 26
    ev = _evaluate(blocks=2, k=4)
    p = ev["provenance"]
    assert (p["design"], p["protocol_id"], p["prompt_hash"], p["contract"]) == (
        E.DESIGN, P.PROTOCOL_ID, P.prompt_hash(), adapters.CONTRACT)
    assert p["code_version"] == P.code_version() and p["dependency_versions"]["python"]
    assert p["key_id"] == E.key_id(KEY) and p["key_id"] not in SECRET and len(p["set_id"]) == 32
    assert p["environment"]["category_names"] == E.CATEGORY_NAMES[:4] and p["environment"]["total_points"] == 100
    for t in ev["trials"]:
        assert t["fingerprint"] == E.fingerprint(t["resource_pool"], t["valuation_A"], t["valuation_B"])
        assert t["ended_at"] >= t["started_at"]
    rebuilt = [E.scenario(KEY, p["set_id"], t["block"], t["cell"], 4)["fingerprint"] for t in ev["trials"]]
    assert rebuilt == [t["fingerprint"] for t in ev["trials"]]  # reproducible from key + set id


# ---------------------------------------------------------------- data isolation (behavioural)

def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")) if root.exists() else []:
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return h.hexdigest()


def test_lab_runs_leave_research_data_and_views_untouched(tmp_path):  # 27
    from tests.test_dashboard_data import _db
    research = tmp_path / "results"
    research.mkdir()
    _db(research / "pilot.db", "pilot", {"baseline_v1": [dict(seed=30000, outcome="agreed")],
                                         "structured_v1": [dict(seed=30000, outcome="walked_away")]})
    repo_results = Path(__file__).resolve().parents[1] / "results"
    before = (_tree_digest(research), _tree_digest(repo_results), json.dumps(D.Store(research).snapshot(), sort_keys=True, default=str))
    ev = _evaluate(blocks=1)
    after = (_tree_digest(research), _tree_digest(repo_results), json.dumps(D.Store(research).snapshot(), sort_keys=True, default=str))
    assert before == after and ev["id"] not in after[2] and "agent-lab" not in after[2]
    assert E.LAB_DIR.parent.name == "sandbox_runs" and list(E.LAB_DIR.glob("*.json"))


# ---------------------------------------------------------------- metrics (hand-calculated)

POOL = {"x": 10, "y": 10}
VA, VB = {"x": 6.0, "y": 4.0}, {"x": 2.0, "y": 8.0}  # 100 points each over the pool


def _offer(turn, actor, a_gets):
    alloc = {"A": a_gets, "B": {c: POOL[c] - a_gets[c] for c in POOL}}
    return TranscriptTurn(turn, actor, NegotiationAction(action_type="OFFER", allocation=alloc))


def _record(turns, outcome, final=None, first="A", rounds=None):
    return NegotiationRecord(instance_seed=0, method="m", model_A="a", model_B="b", first_mover=first,
                             outcome=outcome, num_rounds=rounds or len(turns), final_allocation=final,
                             utility_A=0, utility_B=0, total_welfare=0, optimal_welfare=140,
                             resource_pool=POOL, valuation_A=VA, valuation_B=VB, transcript=turns)


def test_behavior_metrics_by_hand():
    t1 = _offer(1, "A", {"x": 8, "y": 6})   # A: 48 + 24 = 72  -> .72
    t2 = _offer(2, "B", {"x": 6, "y": 2})   # B gets x4 y8: 8 + 64 = 72 -> .72
    t3 = _offer(3, "A", {"x": 7, "y": 3})   # A: 42 + 12 = 54 -> .54 ; B: x3 y7 = 6 + 56 = 62 -> .62
    t4 = TranscriptTurn(4, "B", NegotiationAction(action_type="ACCEPT"))
    r = _record([t1, t2, t3, t4], "agreed", final=t3.action.allocation)
    assert LM.own_offer_values(r, "A") == pytest.approx([0.72, 0.54])
    assert LM.opening_demand(r, "A") == pytest.approx(0.72)
    assert LM.total_concession(r, "A") == pytest.approx(0.18)
    assert LM.concession_frequency(r, "A") == 1.0
    assert LM.accepted_value(r, "A") is None and LM.accepted_value(r, "B") == pytest.approx(0.62)
    assert LM.opening_demand(r, "B") == pytest.approx(0.72)
    assert LM.total_concession(r, "B") is None and LM.concession_frequency(r, "B") is None
    assert LM.behavior(r, "B")["offers"] == 1


def test_hardening_and_mixed_concession_by_hand():
    offers = [_offer(1, "A", {"x": 5, "y": 5}), _offer(3, "A", {"x": 7, "y": 5}), _offer(5, "A", {"x": 6, "y": 5})]
    r = _record(offers, "timeout", rounds=10)  # 50 -> 62 (hardened) -> 56 (conceded)
    assert LM.total_concession(r, "A") == pytest.approx(-0.06)
    assert LM.concession_frequency(r, "A") == pytest.approx(0.5)
    assert LM.opening_demand(r, "B") is None


def test_ended_by_uses_the_transcript_and_the_alternation_rule():
    walk = _record([_offer(1, "B", {"x": 5, "y": 5}), TranscriptTurn(2, "A", NegotiationAction(action_type="WALK_AWAY"))],
                   "walked_away", first="B")
    assert LM.ended_by(walk) == "A"
    assert LM.ended_by(_record([_offer(1, "B", {"x": 5, "y": 5})], "invalid_action", first="B", rounds=2)) == "A"
    assert LM.ended_by(_record([], "invalid_action", first="B", rounds=1)) == "B"
    assert LM.ended_by(_record([], "timeout", rounds=10)) is None


def test_distributions_report_small_samples_honestly():
    assert E.dist([]) == {"n": 0}
    small = E.dist([0.2, None, 0.4])
    assert small["n"] == 2 and small["median"] == pytest.approx(0.3) and small["spread"] == "insufficient observations"
    big = E.dist([0, 1, 2, 3, 4])
    assert (big["q1"], big["median"], big["q3"], big["mean"]) == (1, 2, 3, 2)


def test_example_agent_follows_the_contract():
    obs = adapters.observation(adapters.PROBE_VIEW, "baseline_v1")
    assert decide(obs)["action_type"] == "OFFER"
    assert decide({**obs, "allowed_actions": ["WALK_AWAY"], "round": 10})["action_type"] == "WALK_AWAY"
