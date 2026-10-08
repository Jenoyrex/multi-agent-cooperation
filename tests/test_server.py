"""Production-safe hosting (Phase 3, step 1): host/port settings, public Host/Origin
validation, connection timeout, thread cap, no directory listings, security headers,
/healthz. Every server here runs on 127.0.0.1; nothing reaches an external network."""
import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.client import HTTPConnection
from pathlib import Path

import pytest

from dashboard import server

PUBLIC = "demo.example.org"
ROOT = Path(__file__).resolve().parents[1]


def _serve(public=(), timeout=None, max_connections=server.MAX_CONNECTIONS):
    attrs = {"public_hosts": frozenset(public)}
    if timeout is not None:
        attrs["timeout"] = timeout
    handler = type("H", (server.Handler,), attrs)
    srv = server.LabServer(("127.0.0.1", 0), partial(handler, directory=str(server.STATIC)), max_connections)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _request(port, method, path, headers=(), body=None):
    """headers: (name, value) pairs, so a test can send a header twice. No Host is added unless given."""
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for name, value in headers:
        c.putheader(name, value)
    if body is not None:
        c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    return r.status, r.headers, r.read()


@pytest.fixture
def pub():
    srv = _serve(public=[PUBLIC])
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


# ---------------------------------------------------------------- host / port settings

def test_defaults_stay_local():
    s = server.settings([], {})
    assert (s.host, s.port, s.public_hosts) == ("127.0.0.1", 8765, frozenset())
    assert server.Handler.public_hosts == frozenset()  # nothing configured = localhost only


def test_host_flag_is_respected():
    assert server.settings(["--host", "0.0.0.0"], {}).host == "0.0.0.0"


def test_port_precedence_flag_then_env_then_default():
    assert server.settings([], {"PORT": "10000"}).port == 10000
    assert server.settings(["--port", "9000"], {"PORT": "10000"}).port == 9000
    assert server.settings([], {"PORT": "  "}).port == 8765


@pytest.mark.parametrize("argv, env", [([], {"PORT": "http"}), ([], {"PORT": "-1"}), (["--port", "70000"], {}),
                                       ([], {"PORT": "70000"})])
def test_bad_port_stops_the_server(argv, env):
    with pytest.raises(SystemExit):
        server.settings(argv, env)


def test_public_hosts_are_exact_normalized_names():
    assert server.settings([], {"PUBLIC_HOSTS": " Demo.Example.ORG , b.onrender.com,"}).public_hosts == {
        "demo.example.org", "b.onrender.com"}


@pytest.mark.parametrize("value", ["*.onrender.com", "https://demo.example.org", "demo.example.org:443",
                                   "demo.example.org/x", "localhost", "demo..example.org", "-x.example.org"])
def test_unsafe_public_hosts_stop_the_server(value):
    with pytest.raises(SystemExit):
        server.settings([], {"PUBLIC_HOSTS": value})


def test_entry_point_uses_host_port_env_and_public_hosts():
    """The real `python -m dashboard.server` process, launched the way a host would launch it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "PORT": str(port), "PUBLIC_HOSTS": PUBLIC, "PYTHONPATH": str(ROOT),
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "dashboard.server", "--host", "127.0.0.1"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                status, _, body = _request(port, "GET", "/healthz", [("Host", PUBLIC)])
                break
            except OSError:
                assert proc.poll() is None and time.monotonic() < deadline, proc.stdout.read().decode()
                time.sleep(0.2)
        assert (status, json.loads(body)) == (200, {"status": "ok"})
        assert _request(port, "GET", "/api/lab/info", [("Host", PUBLIC)])[0] == 200
        assert _request(port, "GET", "/api/lab/info", [("Host", "evil.example")])[0] == 403
    finally:
        proc.terminate()
        proc.wait(10)
    assert f"http://127.0.0.1:{port}" in proc.stdout.read().decode()


# ---------------------------------------------------------------- public Host / Origin

def test_public_host_is_served_and_foreign_hosts_are_not(pub):
    ok = lambda host: _request(pub, "GET", "/api/lab/info", [("Host", host)])[0]  # noqa: E731
    assert ok(PUBLIC) == 200 and ok("DEMO.example.org") == 200
    assert ok(f"127.0.0.1:{pub}") == 200 and ok(f"localhost:{pub}") == 200  # local use unchanged
    for foreign in ("evil.example", f"{PUBLIC}.evil.example", f"evil.{PUBLIC}", f"{PUBLIC}:443", f"{PUBLIC}.",
                    f"127.0.0.1:{pub + 1}", ""):
        assert ok(foreign) == 403, foreign


def test_no_public_host_unless_configured():
    srv = _serve()
    try:
        assert _request(srv.server_address[1], "GET", "/api/lab/info", [("Host", PUBLIC)])[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_forwarded_headers_and_duplicate_hosts_cannot_bypass_the_host_check(pub):
    spoof = [("X-Forwarded-Host", PUBLIC), ("X-Forwarded-Proto", "https"), ("X-Forwarded-For", "127.0.0.1"),
             ("Forwarded", f"host={PUBLIC};proto=https")]
    assert _request(pub, "GET", "/api/lab/info", [("Host", "evil.example"), *spoof])[0] == 403
    assert _request(pub, "GET", "/api/lab/info", [("Host", "evil.example"), ("Host", PUBLIC)])[0] == 403
    assert _request(pub, "GET", "/api/lab/info", [("Host", PUBLIC), ("Host", "evil.example")])[0] == 403
    assert _request(pub, "GET", "/api/lab/info", spoof)[0] == 403  # no Host at all


def _post(port, host, origin=None, extra=()):
    """POST /api/lab/probe with an empty object: past the guard it is a harmless 400 (no agent), never a request out."""
    headers = [("Host", host), ("Content-Type", "application/json"), *extra]
    if origin is not None:
        headers.append(("Origin", origin))
    return _request(port, "POST", "/api/lab/probe", headers, b"{}")[0]


def test_public_post_needs_the_matching_https_origin(pub):
    assert _post(pub, PUBLIC, f"https://{PUBLIC}") == 400             # through the guard
    assert _post(pub, "Demo.Example.org", f"https://{PUBLIC}") == 400
    for origin in (None, f"http://{PUBLIC}", "https://evil.example", f"https://{PUBLIC}.evil.example",
                   f"https://{PUBLIC}:443", "null", f"https://{PUBLIC}/"):
        assert _post(pub, PUBLIC, origin) == 403, origin


def test_forwarded_headers_cannot_bypass_the_origin_check(pub):
    fwd = [("X-Forwarded-Proto", "https"), ("X-Forwarded-Host", PUBLIC), ("Forwarded", "proto=https")]
    assert _post(pub, PUBLIC, f"http://{PUBLIC}", fwd) == 403
    assert _post(pub, "evil.example", "https://evil.example", fwd) == 403
    assert _post(pub, "evil.example", f"https://{PUBLIC}", fwd) == 403


def test_local_post_rules_are_unchanged(pub):
    local = f"127.0.0.1:{pub}"
    assert _post(pub, local) == 400 and _post(pub, local, f"http://{local}") == 400
    assert _post(pub, local, f"https://{local}") == 403 and _post(pub, local, "http://evil.example") == 403


# ---------------------------------------------------------------- refused POSTs: the answer always arrives

REFUSALS = {  # name -> (host, extra headers, path, status, answer); "local" = 127.0.0.1:<port>
    "public host, no Origin": (PUBLIC, [("Content-Type", "application/json")], "/api/lab/probe", 403,
                               {"error": "cross-origin request refused"}),
    "public host, http Origin": (PUBLIC, [("Content-Type", "application/json"), ("Origin", f"http://{PUBLIC}")],
                                 "/api/lab/probe", 403, {"error": "cross-origin request refused"}),
    "foreign host": ("evil.example", [("Content-Type", "application/json"), ("Origin", "https://evil.example")],
                     "/api/lab/evaluations", 403, {"error": "cross-origin request refused"}),
    "unknown route": ("local", [("Content-Type", "application/json")], "/api/no/such/route", 405,
                      {"error": "method not allowed"}),
    "probe in the public demo": ("local", [("Content-Type", "application/json")], "/api/lab/probe", 405,
                                 {"error": "method not allowed"}),
    "wrong Content-Type": ("local", [("Content-Type", "text/plain")], "/api/lab/evaluations", 415,
                           {"error": "expected application/json"}),
}
REPEATS = 150


@pytest.mark.parametrize("name", REFUSALS)
@pytest.mark.parametrize("size", [2, 8 * 1024])
def test_refused_posts_always_get_their_answer_not_a_reset(pub, monkeypatch, name, size):
    """Before the fix, closing with the body unread reset ~0.5-2.5% of these connections on Windows."""
    if name == "probe in the public demo":
        monkeypatch.setenv("PUBLIC_DEMO", "1")
    host, headers, path, status, answer = REFUSALS[name]
    host = f"127.0.0.1:{pub}" if host == "local" else host
    body = b"{" + b" " * (size - 2) + b"}"
    for i in range(REPEATS):
        got_status, _, got = _request(pub, "POST", path, [("Host", host), *headers], body)
        assert (got_status, json.loads(got)) == (status, answer), (name, size, i)


def test_oversized_posts_always_get_their_answer_not_a_reset(pub):
    body = b"{" + b" " * (server.MAX_BODY_BYTES + 3616) + b"}"  # ~20 KiB: over the route limit, under the drain cap
    for i in range(REPEATS):
        status, _, got = _request(pub, "POST", "/api/lab/evaluations",
                                  [("Host", f"127.0.0.1:{pub}"), ("Content-Type", "application/json")], body)
        assert (status, json.loads(got)) == (413, {"error": "request body missing or too large"}), i


@pytest.mark.parametrize("declared", ["50000", str(10 ** 9), "9" * 5000], ids=["50k", "1e9", "5000-digits"])
def test_a_lying_content_length_cannot_hold_the_thread(pub, declared):
    """Declares a body it never sends: the refusal still arrives, after at most DRAIN_MAX_S of waiting.
    (5000 digits used to crash int() and drop the connection; now it is simply too large.)"""
    with socket.create_connection(("127.0.0.1", pub), timeout=10) as s:
        s.sendall(f"POST /api/lab/evaluations HTTP/1.1\r\nHost: 127.0.0.1:{pub}\r\nContent-Type: application/json\r\n"
                  f"Content-Length: {declared}\r\n\r\n{{}}".encode())
        start = time.monotonic()
        reply = s.recv(4096)
        assert reply.startswith(b"HTTP/1.0 413") and time.monotonic() - start < server.DRAIN_MAX_S + 1.5


@pytest.mark.parametrize("value", ["abc", "-5", "1e3", "١٢", ""])
def test_malformed_content_length_is_refused_at_once(pub, value):  # not a length: nothing is read or awaited
    with socket.create_connection(("127.0.0.1", pub), timeout=10) as s:
        header = f"Content-Length: {value}\r\n" if value else ""
        s.sendall(f"POST /api/lab/evaluations HTTP/1.1\r\nHost: 127.0.0.1:{pub}\r\nContent-Type: application/json\r\n"
                  f"{header}\r\n".encode())
        start = time.monotonic()
        assert s.recv(4096).startswith(b"HTTP/1.0 413") and time.monotonic() - start < 1.0


def test_accepted_posts_are_unchanged(pub):
    """The success path still reads exactly Content-Length bytes and answers from the route."""
    status, _, got = _request(pub, "POST", "/api/lab/probe",
                              [("Host", f"127.0.0.1:{pub}"), ("Content-Type", "application/json")], b"{}")
    assert status == 400 and "choose an agent" in json.loads(got)["error"]


# ---------------------------------------------------------------- connection timeout and thread cap

def test_connection_timeout_is_finite_and_separate_from_the_turn_deadline():
    assert server.Handler.timeout == server.CONNECTION_TIMEOUT_S and 0 < server.CONNECTION_TIMEOUT_S <= 60


def test_a_stalled_client_is_disconnected():
    srv = _serve(timeout=0.5)
    try:
        with socket.create_connection(srv.server_address, timeout=5) as s:
            s.sendall(b"GET / HTTP/1.1\r\nHost: 127")  # never finishes the request
            start = time.monotonic()
            assert s.recv(1024) == b"" and time.monotonic() - start < 3  # the server hung up
    finally:
        srv.shutdown()
        srv.server_close()


def test_thread_cap_answers_503_and_recovers():
    assert server.MAX_CONNECTIONS > 0
    srv = _serve(timeout=3, max_connections=2)
    port = srv.server_address[1]
    try:
        idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]  # hold both threads
        time.sleep(0.3)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as extra:
            assert extra.recv(1024).startswith(b"HTTP/1.0 503")
        for s in idle:
            s.close()
        deadline = time.monotonic() + 5
        while _request(port, "GET", "/healthz", [("Host", f"127.0.0.1:{port}")])[0] != 200:
            assert time.monotonic() < deadline
            time.sleep(0.1)
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- static files

@pytest.mark.parametrize("path", ["/fonts/", "/vendor/"])
def test_static_directories_are_not_listed(pub, path):
    status, _, body = _request(pub, "GET", path, [("Host", PUBLIC)])
    assert status == 404 and b"woff2" not in body and b"motion" not in body


def test_static_files_are_still_served(pub):
    for path, marker in (("/", b"<!doctype html>"), ("/app.js", b"import"), ("/vendor/motion.js", b"Motion"),
                         ("/fonts/barlow-latin-400-normal.woff2", b"wOF2")):
        status, _, body = _request(pub, "GET", path, [("Host", PUBLIC)])
        assert status == 200 and marker in body[:4096], path
    assert _request(pub, "GET", "/missing.js", [("Host", PUBLIC)])[0] == 404
    assert _request(pub, "GET", "/../pyproject.toml", [("Host", PUBLIC)])[0] == 404


# ---------------------------------------------------------------- security headers

def test_security_headers_on_every_kind_of_response(pub):
    for method, path in (("GET", "/"), ("GET", "/app.js"), ("GET", "/api/lab/info"), ("GET", "/missing.js"),
                         ("GET", "/fonts/"), ("GET", "/healthz"), ("POST", "/nowhere")):
        _, h, _ = _request(pub, method, path, [("Host", PUBLIC)])
        assert h["X-Content-Type-Options"] == "nosniff", path
        assert h["X-Frame-Options"] == "DENY" and h["Content-Security-Policy"] == server.CSP, path


def test_csp_is_same_origin_and_allows_only_the_known_inline_script():
    csp = dict(d.split(" ", 1) for d in server.CSP.split("; "))
    assert "*" not in server.CSP and "http" not in server.CSP and "unsafe-eval" not in server.CSP
    assert csp["default-src"] == "'self'" and csp["frame-ancestors"] == "'none'" and csp["object-src"] == "'none'"
    html = (server.STATIC / "index.html").read_bytes()
    inline = re.findall(rb"<script>(.*?)</script>", html, re.S)
    assert len(inline) == 1  # the theme script; a new inline script needs its hash here too
    digest = base64.b64encode(hashlib.sha256(inline[0]).digest()).decode()
    assert csp["script-src"] == f"'self' 'sha256-{digest}'"  # no 'unsafe-inline' for scripts
    for src in re.findall(rb'<script src="([^"]+)"', html) + re.findall(rb'<link rel="stylesheet" href="([^"]+)"', html):
        assert not src.startswith((b"http:", b"https:", b"//")), src  # every script and stylesheet is same-origin


# ---------------------------------------------------------------- /healthz

def test_healthz_is_tiny_and_reveals_nothing(pub, monkeypatch):
    monkeypatch.setenv("LAB_SCENARIO_SECRET", "ab" * 32)
    for host in (PUBLIC, f"127.0.0.1:{pub}"):
        status, h, body = _request(pub, "GET", "/healthz", [("Host", host)])
        assert status == 200 and json.loads(body) == {"status": "ok"}
        assert h.get_all("Cache-Control") == ["no-store"]
