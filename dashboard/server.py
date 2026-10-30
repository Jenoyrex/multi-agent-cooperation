"""Negotiation Lab server (stdlib only).

    PYTHONPATH=. python -m dashboard.server [--host 127.0.0.1] [--port 8765] [--results results]

Listens on 127.0.0.1 unless --host says otherwise (behind a proxy: --host 0.0.0.0).
Port: --port if given, else $PORT (set by hosts such as Render), else 8765.

Serves the static UI from dashboard/static and two groups of JSON endpoints.
Every /api/* request must name this machine in its Host header (127.0.0.1 or
localhost on this port), so a DNS-rebinding page cannot read or start anything.
Behind an HTTPS proxy, the exact hostnames in $PUBLIC_HOSTS (comma-separated,
no wildcards) are accepted too, and a POST to them needs the matching https://
Origin. X-Forwarded-* headers are never read. On Render, $RENDER_EXTERNAL_HOSTNAME (set by the platform) is added to them.
Neither set = local only.

  GET /healthz                 200 {"status": "ok"}; touches no data, for load-balancer health checks

Research data, read-only (GET only):
  GET /api/snapshot            everything except transcripts
  GET /api/negotiation?key=K   one negotiation with its annotated transcript
These never start, modify or rerun an experiment; the runner scripts remain
the only way to produce research data.

Sandbox, separate (dashboard/sandbox.py; stored under sandbox_runs/):
  GET  /api/sandbox/info       supported agents, limits, key presence
  GET  /api/sandbox/runs       finished sandbox runs, newest first
  GET  /api/sandbox/runs/ID    one sandbox run, live while it runs
  POST /api/sandbox/runs       start one sandbox negotiation (same-origin JSON only)

Agent Lab, separate (lab/; stored under sandbox_runs/lab/):
  GET  /api/lab/info           whether evaluations can start (scenario secret configured)
  GET  /api/lab/evaluations    evaluation headers, newest first (never endpoint URLs)
  GET  /api/lab/evaluations/ID one evaluation; while ANY evaluation runs, progress only (sealed)
  POST /api/lab/evaluations    start a repeated, role-balanced evaluation
  POST /api/lab/probe          send one synthetic turn to an HTTP agent (absent when PUBLIC_DEMO is on)

PUBLIC_DEMO=1 (dashboard/demo.py): scripted agents only, at most 3 lab blocks,
no probe, no technical error detail in responses. Unset = everything above.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import re
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from dashboard import demo, sandbox
from dashboard.data import RESULTS_DIR, Store
from lab import adapters, evaluation
from workbench import api as workbench_api
from workbench.agents import WorkbenchError

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8765
CONNECTION_TIMEOUT_S = 15  # per socket read/write of one HTTP connection (not the Agent Lab turn deadline)
MAX_CONNECTIONS = 32       # concurrent request threads; one more connection gets 503 and is closed
MAX_BODY_BYTES = 16_384    # largest POST body a route will read
UPLOAD_BODY_BYTES = 128 * 1024  # routes that carry an uploaded agent file (48 KB of source, JSON-escaped)
UPLOAD_ROUTES = frozenset({"/api/workbench/uploads/inspect", "/api/workbench/agents"})
DRAIN_MAX_BYTES, DRAIN_MAX_S = 64 * 1024, 2.0  # most a refused POST's body is read and discarded
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
BUSY = (b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n"
        b"Retry-After: 1\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\nbusy\n")


def public_hosts(value: str) -> frozenset:
    """$PUBLIC_HOSTS -> exact lowercase hostnames. No scheme, port or wildcard: anything else is refused."""
    hosts = {h.strip().lower() for h in value.split(",") if h.strip()}
    if bad := sorted(h for h in hosts if not HOSTNAME_RE.match(h)):
        raise ValueError(f"PUBLIC_HOSTS must list plain hostnames (no scheme, port or wildcard): {', '.join(bad)}")
    return frozenset(hosts)


def _inline_script_hashes(html: bytes) -> list[str]:
    """CSP hashes of index.html's inline scripts, from the bytes as served (so line endings can't break them)."""
    return [f"'sha256-{base64.b64encode(hashlib.sha256(s).digest()).decode()}'"
            for s in re.findall(rb"<script>(.*?)</script>", html, re.S)]


# Everything is same-origin. Styles need 'unsafe-inline': the templates use style="" attributes and
# Motion's view transitions insert a <style> element. Scripts do not: files from 'self' plus the one
# inline theme script by hash. data: only for the inline SVG favicon.
CSP = "; ".join([
    "default-src 'self'",
    " ".join(["script-src 'self'", *_inline_script_hashes((STATIC / "index.html").read_bytes())]),
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:", "font-src 'self'", "media-src 'self'", "connect-src 'self'",
    "object-src 'none'", "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'",
])
SECURITY_HEADERS = (("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", CSP),
                    ("X-Frame-Options", "DENY"))


def _probe(req):
    """Agent Lab 'Test connection': one synthetic turn to an HTTP endpoint."""
    spec, token = adapters.validate_agent(req.get("agent") if isinstance(req, dict) else None, "This agent")
    if spec["kind"] != "http":
        raise adapters.SpecError("Only Custom HTTP endpoints need a connection test.")
    return asyncio.run(adapters.probe(spec, token)), 200


# POST path -> handler(body) -> (payload, status). Every route sits behind the same guard in do_POST.
POST_ROUTES = {
    "/api/sandbox/runs": lambda req: ({"id": sandbox.start(req).id}, 202),
    "/api/lab/evaluations": lambda req: ({"id": evaluation.start(req).id}, 202),
    "/api/lab/probe": _probe,
}
# Routes that do not exist in the public demo: the probe would let a visitor aim server requests anywhere.
DEMO_ABSENT_ROUTES = frozenset({"/api/lab/probe"})


def _clean(o):
    """NaN/inf are not valid JSON; show them as missing."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


class Handler(SimpleHTTPRequestHandler):
    store: Store
    public_hosts: frozenset = frozenset()  # set from $PUBLIC_HOSTS by main(); empty = local only
    timeout = CONNECTION_TIMEOUT_S

    def _host_kind(self) -> Optional[str]:
        """'local' if the Host header names this machine (a DNS-rebinding page sends its own name),
        'public' if it is exactly one of public_hosts, else None. One Host header only;
        X-Forwarded-* is never consulted, so a client cannot talk its way in."""
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return None
        host, port = hosts[0], self.server.server_address[1]
        if host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return "local"
        return "public" if host.lower() in self.public_hosts else None

    def do_GET(self):
        try:
            return self._get()
        except Exception:  # noqa: BLE001 — always answer (with the security headers); the traceback goes to the server log only
            traceback.print_exc()
            return self._json({"error": "internal error"}, 500)

    def _get(self):
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self._json({"status": "ok"})
        if url.path.startswith("/api/") and not self._host_kind():
            return self._json({"error": "cross-origin request refused"}, 403)
        if url.path == "/api/lab/info":
            error = evaluation.ready()
            return self._json({"ready": error is None, "error": error, "max_blocks": evaluation.max_blocks(),
                               "sealed": evaluation.sealed(), **demo.flags(),
                               **({"max_lab_blocks": evaluation.DEMO_MAX_BLOCKS} if demo.enabled() else {})})
        if url.path == "/api/snapshot":
            return self._json(self.store.snapshot())
        if url.path == "/api/negotiation":
            detail = self.store.negotiation(parse_qs(url.query).get("key", [""])[0])
            return self._json(detail) if detail else self._json({"error": "not found"}, 404)
        if url.path == "/api/sandbox/info":
            return self._json(sandbox.info())
        if url.path == "/api/sandbox/runs":
            return self._json(sandbox.listing())
        if url.path.startswith("/api/sandbox/runs/"):
            run = sandbox.get(url.path.rsplit("/", 1)[1])
            return self._json(run) if run else self._json({"error": "not found"}, 404)
        if url.path == "/api/lab/evaluations":
            return self._json(evaluation.listing())
        if url.path.startswith("/api/lab/evaluations/"):
            ev = evaluation.get(url.path.rsplit("/", 1)[1])
            return self._json(ev) if ev else self._json({"error": "not found"}, 404)
        if url.path.startswith("/api/workbench/"):
            return self._json(*workbench_api.get(url.path))
        if url.path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    def _drain(self, length: int) -> None:
        """Read and discard a body this handler will not use, so closing the socket afterwards does
        not reset the connection before the client has read the answer. Bounded in bytes
        (DRAIN_MAX_BYTES) and in total time (DRAIN_MAX_S): a client that lies about its
        Content-Length or sends slowly cannot hold the thread."""
        left, deadline = min(length, DRAIN_MAX_BYTES), time.monotonic() + DRAIN_MAX_S
        try:
            while left > 0 and (remaining := deadline - time.monotonic()) > 0:
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(min(left, 8192))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:  # timed out or reset: the answer is still sent, the socket then closes
            pass
        finally:
            self.connection.settimeout(self.timeout)

    def _refuse(self, length: int, payload: dict, status: int):
        self._drain(length)
        return self._json(payload, status)

    def do_POST(self):
        raw = self.headers.get("Content-Length", "")  # absent or not a number: no body to read
        path = urlparse(self.path).path
        limit = UPLOAD_BODY_BYTES if path in UPLOAD_ROUTES else MAX_BODY_BYTES
        length = (int(raw) if len(raw) <= 9 else limit + 1) if raw.isascii() and raw.isdigit() else 0
        route = None if demo.enabled() and path in DEMO_ABSENT_ROUTES else POST_ROUTES.get(path) or workbench_api.post_route(path)
        if route is None:  # an absent route does nothing with the body but discard it
            return self._refuse(length, {"error": "method not allowed"}, 405)
        # Starting a run costs money: only this page may do it. A cross-site form or
        # fetch cannot send application/json without a preflight this server never grants.
        # The Host must be this machine too, or a DNS-rebinding page would match its own Origin.
        # A public host is reached only through the HTTPS proxy, so its page always sends an
        # https:// Origin; one is required there (a missing Origin is refused).
        kind, host, origin = self._host_kind(), self.headers.get("Host", ""), self.headers.get("Origin")
        allowed = ((kind == "local" and (not origin or origin == f"http://{host}"))
                   or (kind == "public" and origin == f"https://{host.lower()}"))
        if not allowed:
            return self._refuse(length, {"error": "cross-origin request refused"}, 403)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._refuse(length, {"error": "expected application/json"}, 415)
        if not 0 < length <= limit:
            return self._refuse(length, {"error": "request body missing or too large"}, 413)
        try:
            return self._json(*route(json.loads(self.rfile.read(length))))
        except json.JSONDecodeError:
            return self._json({"error": "The request was not valid JSON."}, 400)
        except demo.RateLimited as exc:  # public demo only; raised before a run or evaluation exists
            return self._json({"error": str(exc)}, 429, [("Retry-After", str(exc.retry_after))])
        except WorkbenchError as exc:  # names the field to correct
            return self._json(exc.payload(), exc.status)
        except (sandbox.SandboxError, adapters.SpecError) as exc:
            busy = demo.enabled() and isinstance(exc, sandbox.BusyError)  # local answers keep their 400
            return self._json({"error": str(exc)}, 409 if busy else 400)
        except Exception:  # noqa: BLE001 — always answer; the traceback goes to the server log only
            traceback.print_exc()
            return self._json({"error": "internal error"}, 500)

    def _json(self, payload, status=200, headers=()):
        body = json.dumps(_clean(payload)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def list_directory(self, path):  # static directories are never indexed
        self.send_error(404, "Not found")
        return None

    def end_headers(self):
        if not getattr(self, "path", "").startswith(("/api/", "/healthz")):  # JSON answers set no-store
            self.send_header("Cache-Control", "no-cache")
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        super().end_headers()

    def log_message(self, *args):  # quiet
        pass


class LabServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with at most `max_connections` request threads. A connection
    beyond that is answered 503 and closed at once instead of queueing a new thread."""

    def __init__(self, address, handler, max_connections: int = MAX_CONNECTIONS):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(BUSY)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def settings(argv=None, environ=None) -> argparse.Namespace:
    """Command line plus environment. Host: --host, default 127.0.0.1. Port: --port if given,
    else $PORT, else 8765. Public hosts: $PUBLIC_HOSTS. Bad values stop the server at start."""
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"interface to listen on (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=None, help=f"default: $PORT, else {DEFAULT_PORT}")
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    if args.port is None:
        env_port = environ.get("PORT", "").strip()
        if env_port and not env_port.isdigit():
            parser.error(f"PORT must be a whole number, got {env_port!r}")
        args.port = int(env_port) if env_port else DEFAULT_PORT
    if not 0 <= args.port <= 65535:
        parser.error(f"port {args.port} is out of range")
    try:
        # On Render, the service's own public hostname is provided as RENDER_EXTERNAL_HOSTNAME; it is accepted too.
        args.public_hosts = public_hosts(",".join(filter(None, (environ.get("PUBLIC_HOSTS", ""), environ.get("RENDER_EXTERNAL_HOSTNAME", "")))))
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main():
    args = settings()
    Handler.store = Store(args.results)
    Handler.public_hosts = args.public_hosts
    server = LabServer((args.host, args.port), partial(Handler, directory=str(STATIC)))
    public = f"  - public hosts: {', '.join(sorted(args.public_hosts))}" if args.public_hosts else ""
    print(f"Negotiation Lab on http://{args.host}:{server.server_address[1]}  - research data (read-only): "
          f"{args.results}  - sandbox runs: {sandbox.SANDBOX_DIR}{public}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
