"""Sandbox layer: validation, a full engine run with scripted agents (no API),
storage kept apart from research results, and the server's POST guard."""
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from dashboard import sandbox as S
from dashboard import server

REQ = {"agents": {"A": "scripted", "B": "scripted"}, "pool": {"apples": 10, "books": 8, "tickets": 6},
       "preferences": {"A": {"apples": 50, "books": 30, "tickets": 20}, "B": {"apples": 10, "books": 30, "tickets": 60}},
       "strategy": "structured_v1", "first_mover": "B"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SANDBOX_DIR", tmp_path / "sandbox_runs")
    monkeypatch.setattr(S, "SCRIPTED_PACE_S", 0)
    monkeypatch.setattr(S, "_runs", {})
    monkeypatch.setattr(S, "_active", None)


@pytest.mark.parametrize("change, msg", [
    ({"agents": {"A": "scripted", "B": "claude"}}, "as a pair"),
    ({"agents": {"A": "gpt-5", "B": "claude"}}, "supported agent"),
    ({"pool": {"apples": 10}}, "between 2 and 5"),
    ({"pool": {"Apples!": 10, "books": 2}}, "lowercase"),
    ({"pool": {"apples": 0, "books": 2}}, "1 to 50"),
    ({"preferences": {"A": {"apples": 50, "books": 30, "tickets": 19}, "B": REQ["preferences"]["B"]}}, "add up to 99"),
    ({"preferences": {"A": {"apples": 50, "books": 50}, "B": REQ["preferences"]["B"]}}, "every resource"),
    ({"strategy": "fairness_v9"}, "baseline or the structured"),
])
def test_validation_refuses_bad_scenarios(change, msg):
    with pytest.raises(S.SandboxError, match=msg):
        S.validate({**REQ, **change})


def test_missing_key_is_refused_before_anything_runs(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(S.SandboxError, match="ANTHROPIC_API_KEY"):
        S.start({**REQ, "agents": {"A": "claude", "B": "claude"}}, background=False)
    assert not S.SANDBOX_DIR.exists()


def test_scripted_run_uses_the_engine_and_stays_out_of_results():
    run = S.start(REQ, background=False)
    v = S.get(run.id)
    assert v["kind"] == "sandbox" and v["status"] == "finished"
    res = v["result"]
    assert res["mode"] == "sandbox" and res["condition"] == "structured_v1" and res["first_mover"] == "B"
    assert res["outcome"] in ("agreed", "walked_away", "timeout", "invalid_action")
    assert res["max_rounds"] == 10 and res["rounds"] <= 10
    # private points became per-unit values with a 100-point ceiling (spec §2)
    assert res["valuation_A"]["apples"] == pytest.approx(5.0)
    assert sum(res["valuation_B"][c] * q for c, q in REQ["pool"].items()) == pytest.approx(100)
    assert [e["actor"] for e in res["events"]][0] == "B"
    assert len(v["turns"]) == len([e for e in res["events"] if e["action"] != "INVALID"])
    saved = list(S.SANDBOX_DIR.glob("*.json"))
    assert [p.stem for p in saved] == [run.id] and "results" not in str(saved[0].parent)
    assert S.listing()[0]["id"] == run.id


@pytest.mark.parametrize("reason, detail, title", [
    ("transport_failure", "AuthenticationError: Error code: 401", "API key rejected"),
    ("transport_failure", "RateLimitError: Error code: 429", "Rate limited"),
    ("transport_failure", "APITimeoutError: Request timed out.", "Request timed out"),
    ("transport_failure", "APIConnectionError: Connection error.", "Provider unreachable"),
    ("transport_failure", "NotFoundError: Error code: 404", "Model unavailable"),
    ("budget_exhausted", "cost budget reached", "Spending cap reached"),
])
def test_aborts_become_plain_messages(reason, detail, title):
    cfg = S.validate({**REQ, "agents": {"A": "claude", "B": "openai"}})
    out = S.explain_abort(reason, detail, "B", cfg)
    assert out["title"] == title and "GPT-4.1" in out["message"]
    assert out["message"].endswith("No research data was modified.")


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _post(port, body, headers):
    c = HTTPConnection("127.0.0.1", port)
    c.request("POST", "/api/sandbox/runs", body=body, headers=headers)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_post_guard_refuses_cross_origin_and_non_json():
    srv = _serve()
    port = srv.server_address[1]
    try:
        body = json.dumps(REQ)
        assert _post(port, body, {"Content-Type": "application/json", "Origin": "http://evil.example"})[0] == 403
        rebound = f"evil.example:{port}"  # DNS rebinding: Origin matches Host, but Host isn't this machine
        assert _post(port, body, {"Content-Type": "application/json", "Host": rebound, "Origin": f"http://{rebound}"})[0] == 403
        assert _post(port, body, {"Content-Type": "text/plain"})[0] == 415
        status, out = _post(port, json.dumps({**REQ, "strategy": "x"}), {"Content-Type": "application/json"})
        assert status == 400 and "structured" in out["error"]
        c = HTTPConnection("127.0.0.1", port)
        c.request("POST", "/api/snapshot", body="{}", headers={"Content-Type": "application/json"})
        assert c.getresponse().status == 405  # research endpoints stay read-only
    finally:
        srv.shutdown()
