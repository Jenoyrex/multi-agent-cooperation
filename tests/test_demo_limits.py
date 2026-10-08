"""PUBLIC_DEMO resource limits (Phase 3, step 3): one global start limit shared by
the sandbox and the Agent Lab, 409 when busy, bounded retention in memory and on
disk, capped listings, cached snapshot provenance. Off, none of it applies.

Offline: a guard fails on any non-loopback connection or DNS lookup."""
import hashlib
import json
import os
import socket
import threading
import time
from functools import partial
from http.client import HTTPConnection
from pathlib import Path

import pytest

from dashboard import data, demo, server
from dashboard import sandbox as S
from lab import adapters, evaluation as E

REQ = {"agents": {"A": "scripted", "B": "scripted"}, "pool": {"apples": 10, "books": 8},
       "preferences": {"A": {"apples": 60, "books": 40}, "B": {"apples": 30, "books": 70}},
       "strategy": "baseline_v1", "first_mover": "A"}
LAB = {"agents": {"X": {"kind": "scripted"}, "Y": {"kind": "scripted"}}, "strategy": "baseline_v1",
       "scenario": {"blocks": 1, "categories": 2}}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
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
    monkeypatch.setenv(E.SECRET_ENV, "e5" * 32)
    monkeypatch.setattr(E, "LAB_DIR", tmp_path / "sandbox_runs" / "lab")
    monkeypatch.setattr(E, "SCRIPTED_PACE_S", 0)
    monkeypatch.setattr(E, "_evals", {})
    monkeypatch.setattr(E, "_active", None)
    monkeypatch.setattr(S, "SANDBOX_DIR", tmp_path / "sandbox_runs")
    monkeypatch.setattr(S, "SCRIPTED_PACE_S", 0)
    monkeypatch.setattr(S, "_runs", {})
    monkeypatch.setattr(S, "_active", None)
    monkeypatch.setattr(server.Handler, "store", data.Store(tmp_path / "results"), raising=False)


@pytest.fixture
def demo_on(monkeypatch):
    monkeypatch.setenv(demo.ENV, "1")


@pytest.fixture
def unlimited(monkeypatch):
    """For tests about retention and listings: the start limit is tested on its own."""
    monkeypatch.setattr(demo, "START_LIMIT", 10 ** 6)


@pytest.fixture
def port():
    srv = server.LabServer(("127.0.0.1", 0), partial(server.Handler, directory=str(server.STATIC)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _post(port, path, body, extra=()):
    c = HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("POST", path, body=body if isinstance(body, bytes) else json.dumps(body).encode(),
              headers={"Content-Type": "application/json", **dict(extra)})
    r = c.getresponse()
    return r.status, r.headers, r.read().decode()


def _get(port, path):
    c = HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read().decode()


def _sandbox_runs(n):
    ids = []
    for _ in range(n):
        ids.append(S.start(REQ, background=False).id)
        time.sleep(0.01)  # distinct creation times and file mtimes
    return ids


def _evaluations(n):
    ids = []
    for _ in range(n):
        ids.append(E.start(LAB, background=False).id)
        time.sleep(0.01)
    return ids


def test_the_policy_numbers():
    assert (demo.START_LIMIT, demo.START_WINDOW_S, demo.RETAIN, demo.LISTING_MAX) == (20, 60.0, 100, 20)
    assert data.PROVENANCE_TTL_S == 60.0
    assert (server.MAX_BODY_BYTES, server.DRAIN_MAX_BYTES, server.DRAIN_MAX_S) == (16_384, 64 * 1024, 2.0)  # unchanged


# ---------------------------------------------------------------- A. global start limit

def test_rolling_window_and_retry_after():
    for i in range(demo.START_LIMIT):
        demo.take_start(now=100.0 + i * 0.1)
    with pytest.raises(demo.RateLimited) as exc:
        demo.take_start(now=130.0)
    assert exc.value.retry_after == 30  # the oldest start leaves the window at 160
    with pytest.raises(demo.RateLimited) as exc:
        demo.take_start(now=159.95)
    assert exc.value.retry_after == 1   # never 0
    demo.take_start(now=160.0)           # the oldest has expired: room for one


def test_sandbox_and_lab_share_one_limit_then_get_429(demo_on, port, monkeypatch):
    sandbox_ids = _sandbox_runs(10)
    lab_ids = _evaluations(10)  # 20 starts in all
    assert len(sandbox_ids) == len(lab_ids) == 10
    runs, evals = dict(S._runs), dict(E._evals)
    for path, body in (("/api/sandbox/runs", REQ), ("/api/lab/evaluations", LAB)):
        status, headers, text = _post(port, path, body)
        assert status == 429 and json.loads(text) == {"error": demo.RateLimited(1).args[0]}
        assert 1 <= int(headers["Retry-After"]) <= 60
    with pytest.raises(demo.RateLimited):
        S.start(REQ, background=False)
    with pytest.raises(demo.RateLimited):
        E.start(LAB, background=False)
    assert S._runs == runs and E._evals == evals and S._active is None and E._active is None  # nothing was created


def test_rate_limited_requests_build_nothing_and_call_nothing(demo_on, port, monkeypatch):
    for _ in range(demo.START_LIMIT):
        demo.take_start()
    calls = []
    for mod, name in ((S, "_agent"), (adapters, "build_agent"), (adapters, "validate_agent")):
        real = getattr(mod, name)
        monkeypatch.setattr(mod, name, lambda *a, _n=name, _r=real, **k: calls.append(_n) or _r(*a, **k))
    # Every thread started meanwhile, by name. Run and evaluation workers are Thread(target=go): "Thread-N (go)";
    # the server's own per-connection threads are "(process_request_thread)" and may still be closing afterwards.
    started, real_start = [], threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name) or real_start(self))
    assert _post(port, "/api/sandbox/runs", REQ)[0] == 429
    assert _post(port, "/api/lab/evaluations", LAB)[0] == 429
    assert "_agent" not in calls and "build_agent" not in calls  # validated, then refused before any agent
    assert S._runs == {} and E._evals == {} and not S.SANDBOX_DIR.exists()
    assert started and not [n for n in started if n.endswith("(go)")], started  # no run or evaluation worker started


def test_forwarded_headers_cannot_buy_more_starts(demo_on, port):
    for _ in range(demo.START_LIMIT):
        demo.take_start()
    for i, spoof in enumerate([("X-Forwarded-For", "203.0.113.7"), ("X-Forwarded-For", "10.0.0.1, 198.51.100.2"),
                               ("Forwarded", "for=192.0.2.60;proto=https"), ("X-Real-IP", "192.0.2.1"),
                               ("True-Client-IP", "192.0.2.2"), ("CF-Connecting-IP", "192.0.2.3")]):
        assert _post(port, "/api/sandbox/runs", REQ, [spoof])[0] == 429, spoof
    assert S._runs == {}


def test_refused_and_busy_requests_do_not_use_up_starts(demo_on, port):
    for _ in range(30):  # invalid for the demo: refused by validation, before the limiter
        assert _post(port, "/api/sandbox/runs", {**REQ, "agents": {"A": "claude", "B": "claude"}})[0] == 400
    busy = S.Run(S.validate(REQ))
    S._active = busy
    try:
        status, _, text = _post(port, "/api/sandbox/runs", REQ)
        assert status == 409 and "still running" in json.loads(text)["error"]
    finally:
        S._active = None
    assert len(demo._starts) == 0 and S._runs == {}


def test_no_limit_when_the_demo_is_off():
    assert len(_sandbox_runs(demo.START_LIMIT + 5)) == 25 and len(demo._starts) == 0


def test_a_rate_limited_request_answers_at_once_and_never_resets(demo_on, port):
    for _ in range(demo.START_LIMIT):
        demo.take_start()
    body = b"{" + b" " * (server.MAX_BODY_BYTES - 2) + b"}"  # the largest body a route reads
    for i in range(150):
        start = time.monotonic()
        status, _, text = _post(port, "/api/sandbox/runs", body)
        assert status in (400, 429) and time.monotonic() - start < 1.0, i  # never waits on a drain
    # the padded body is not a valid request (400); a valid one gets 429, reliably
    for i in range(150):
        status, _, text = _post(port, "/api/sandbox/runs", json.dumps(REQ).encode() + b" " * 8000)
        assert (status, json.loads(text)["error"]) == (429, demo.RateLimited(1).args[0]), i


def test_oversized_and_lying_bodies_stay_bounded_in_the_demo(demo_on, port):
    status, _, _ = _post(port, "/api/sandbox/runs", b"{" + b" " * 20_000 + b"}")
    assert status == 413
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(f"POST /api/lab/evaluations HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
                  f"Content-Length: 50000\r\n\r\n{{}}".encode())
        start = time.monotonic()
        assert s.recv(4096).startswith(b"HTTP/1.0 413") and time.monotonic() - start < server.DRAIN_MAX_S + 1.5
    assert len(demo._starts) == 0


# ---------------------------------------------------------------- B. busy

def test_busy_is_409_in_the_demo_and_still_400_locally(port, monkeypatch):
    for on in (True, False):
        if on:
            monkeypatch.setenv(demo.ENV, "1")
        else:
            monkeypatch.delenv(demo.ENV)
        S._active = S.Run(S.validate(REQ))
        E._active = object()
        try:
            assert _post(port, "/api/sandbox/runs", REQ)[0] == (409 if on else 400)
            assert _post(port, "/api/lab/evaluations", LAB)[0] == (409 if on else 400)
        finally:
            S._active = E._active = None
    assert S._runs == {} and E._evals == {}


# ---------------------------------------------------------------- C/D. retention

def test_memory_and_disk_keep_only_the_newest_sandbox_runs(demo_on, unlimited, monkeypatch, port):
    monkeypatch.setattr(demo, "RETAIN", 5)
    ids = _sandbox_runs(8)
    assert list(S._runs) == ids[-5:]
    assert sorted(p.stem for p in S.SANDBOX_DIR.glob("*.json")) == sorted(ids[-5:])
    for old in ids[:3]:
        assert S.get(old) is None and _get(port, f"/api/sandbox/runs/{old}")[0] == 404
    for new in ids[-5:]:
        assert _get(port, f"/api/sandbox/runs/{new}")[0] == 200


def test_memory_and_disk_keep_only_the_newest_evaluations(demo_on, unlimited, monkeypatch, port):
    monkeypatch.setattr(demo, "RETAIN", 3)
    ids = _evaluations(5)
    assert list(E._evals) == ids[-3:]
    assert sorted(p.stem for p in E.LAB_DIR.glob("*.json")) == sorted(ids[-3:])
    assert _get(port, f"/api/lab/evaluations/{ids[0]}")[0] == 404
    assert _get(port, f"/api/lab/evaluations/{ids[-1]}")[0] == 200


class _R:
    def __init__(self, i, created):
        self.id, self.created = i, created


def test_the_active_report_is_never_evicted():
    store = {f"{i:012x}": _R(f"{i:012x}", created=i) for i in range(10)}
    demo.prune_memory(store, 3, active_id=f"{0:012x}")  # the active one is the oldest
    assert set(store) == {f"{0:012x}", f"{8:012x}", f"{9:012x}"}


def test_disk_retention_is_bounded_deterministic_and_local(tmp_path):
    folder = tmp_path / "runs"
    (folder / "lab").mkdir(parents=True)
    ids = [f"{i:012x}" for i in range(120)]
    for i, rid in enumerate(ids):
        (folder / f"{rid}.json").write_text("{}")
        os.utime(folder / f"{rid}.json", (1_000_000 + i, 1_000_000 + i))
    for other in ("notes.json", "README.txt", "ABCDEF123456.json", "lab/0000000000aa.json"):
        (folder / other).write_text("keep")
    demo.prune_disk(folder, 100, active_id=ids[0])  # the active report is the oldest
    kept = {p.stem for p in folder.glob("*.json") if demo.REPORT_ID.fullmatch(p.stem)}
    assert kept == set(ids[20:]) | {ids[0]}
    for other in ("notes.json", "README.txt", "ABCDEF123456.json", "lab/0000000000aa.json"):
        assert (folder / other).read_text() == "keep"  # only report files, only this folder


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")) if root.exists() else []:
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + p.read_bytes())
    return h.hexdigest()


def test_retention_never_touches_research_results(demo_on, unlimited, monkeypatch):
    monkeypatch.setattr(demo, "RETAIN", 2)
    before = _digest(ROOT / "results")
    _sandbox_runs(4)
    _evaluations(3)
    assert _digest(ROOT / "results") == before


def test_nothing_is_deleted_when_the_demo_is_off(monkeypatch):
    monkeypatch.setattr(demo, "RETAIN", 2)
    monkeypatch.setattr(demo, "LISTING_MAX", 2)
    ids = _sandbox_runs(5)
    assert list(S._runs) == ids and len(list(S.SANDBOX_DIR.glob("*.json"))) == 5 and len(S.listing()) == 5


# ---------------------------------------------------------------- E. listing cap

def test_demo_sandbox_listing_is_the_newest_window(demo_on, unlimited):
    ids = _sandbox_runs(demo.LISTING_MAX + 5)
    listed = [r["id"] for r in S.listing()]
    assert listed == list(reversed(ids))[:demo.LISTING_MAX]


def test_demo_lab_listing_window_and_retained_vs_deleted(demo_on, unlimited, monkeypatch, port):
    monkeypatch.setattr(demo, "RETAIN", 8)
    monkeypatch.setattr(demo, "LISTING_MAX", 5)
    ids = _evaluations(10)
    listed = [r["id"] for r in json.loads(_get(port, "/api/lab/evaluations")[1])]
    assert listed == list(reversed(ids))[:5]
    for rid in ids[2:5]:  # retained, just outside the window
        assert _get(port, f"/api/lab/evaluations/{rid}")[0] == 200
    for rid in ids[:2]:  # evicted
        assert _get(port, f"/api/lab/evaluations/{rid}")[0] == 404


def test_demo_listing_reads_only_the_window_from_disk(demo_on, monkeypatch):
    S.SANDBOX_DIR.mkdir(parents=True)
    for i in range(60):
        rid = f"{i:012x}"
        view = {"id": rid, "created": i, "status": "finished", "config": {}, "labels": {}, "error": None, "result": None}
        (S.SANDBOX_DIR / f"{rid}.json").write_text(json.dumps(view))
        os.utime(S.SANDBOX_DIR / f"{rid}.json", (1_000_000 + i, 1_000_000 + i))
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: reads.append(self.name) or real(self, *a, **k))
    listed = S.listing()
    assert [r["id"] for r in listed] == [f"{i:012x}" for i in range(59, 39, -1)] and len(reads) == demo.LISTING_MAX


# ---------------------------------------------------------------- F. snapshot provenance

@pytest.fixture
def git_calls(monkeypatch):
    calls = []

    def fake():
        calls.append(time.monotonic())
        time.sleep(0.05)
        return "abc123"
    monkeypatch.setattr(data.provenance, "code_version", fake)
    return calls


def test_demo_snapshot_runs_git_once_per_ttl(demo_on, git_calls, port, monkeypatch):
    for _ in range(10):
        status, body = _get(port, "/api/snapshot")
        assert status == 200 and json.loads(body)["provenance"]["code_version"] == "abc123"
    assert len(git_calls) == 1
    monkeypatch.setattr(data, "PROVENANCE_TTL_S", 0.1)
    time.sleep(0.15)
    data.current_provenance()
    assert len(git_calls) == 2  # refreshed after the TTL


def test_concurrent_snapshots_share_one_refresh(demo_on, git_calls):
    barrier = threading.Barrier(16)
    results = []

    def hit():
        barrier.wait()
        results.append(data.current_provenance()["code_version"])
    threads = [threading.Thread(target=hit) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["abc123"] * 16 and len(git_calls) == 1


def test_a_failed_refresh_shows_nothing_of_the_failure(demo_on, monkeypatch, port):
    def boom():
        raise RuntimeError("git exploded at /home/demo/repo")
    monkeypatch.setattr(data.provenance, "code_version", boom)
    status, body = _get(port, "/api/snapshot")
    assert status == 200 and json.loads(body)["provenance"]["code_version"] == "unknown"
    assert "exploded" not in body and "/home/" not in body
    monkeypatch.setattr(data.provenance, "code_version", lambda: "good1")
    monkeypatch.setattr(data, "PROVENANCE_TTL_S", 0.0)
    assert data.current_provenance()["code_version"] == "good1"
    monkeypatch.setattr(data.provenance, "code_version", boom)
    assert data.current_provenance()["code_version"] == "good1"  # the last good value survives a failure


def test_no_cache_when_the_demo_is_off(git_calls):
    data.current_provenance()
    data.current_provenance()
    assert len(git_calls) == 2
