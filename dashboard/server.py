"""Negotiation Lab server (stdlib only, localhost).

    PYTHONPATH=. python -m dashboard.server [--port 8765] [--results results]

Serves the static UI from dashboard/static and two groups of JSON endpoints.
Every /api/* request must name this machine in its Host header (127.0.0.1 or
localhost on this port), so a DNS-rebinding page cannot read or start anything.

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
  POST /api/lab/probe          send one synthetic turn to an HTTP agent
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dashboard import sandbox
from dashboard.data import RESULTS_DIR, Store
from lab import adapters, evaluation

STATIC = Path(__file__).resolve().parent / "static"


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

    def _local_host(self) -> bool:
        """The Host header names this machine. A DNS-rebinding page sends its own name."""
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/api/") and not self._local_host():
            return self._json({"error": "cross-origin request refused"}, 403)
        if url.path == "/api/lab/info":
            return self._json({"ready": evaluation.ready() is None, "error": evaluation.ready(),
                               "max_blocks": evaluation.MAX_BLOCKS, "sealed": evaluation.sealed()})
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
        if url.path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    def do_POST(self):
        route = POST_ROUTES.get(urlparse(self.path).path)
        if route is None:
            return self._json({"error": "method not allowed"}, 405)
        # Starting a run costs money: only this page may do it. A cross-site form or
        # fetch cannot send application/json without a preflight this server never grants.
        # The Host must be this machine too, or a DNS-rebinding page would match its own Origin.
        origin = self.headers.get("Origin")
        if not self._local_host() or (origin and origin != f"http://{self.headers.get('Host')}"):
            return self._json({"error": "cross-origin request refused"}, 403)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json({"error": "expected application/json"}, 415)
        length = self.headers.get("Content-Length", "")
        length = int(length) if length.isdigit() else 0
        if not 0 < length <= 16_384:
            return self._json({"error": "request body missing or too large"}, 413)
        try:
            return self._json(*route(json.loads(self.rfile.read(length))))
        except json.JSONDecodeError:
            return self._json({"error": "The request was not valid JSON."}, 400)
        except (sandbox.SandboxError, adapters.SpecError) as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception:  # noqa: BLE001 — always answer; the traceback goes to the server log only
            traceback.print_exc()
            return self._json({"error": "internal error"}, 500)

    def _json(self, payload, status=200):
        body = json.dumps(_clean(payload)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):  # quiet
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()
    Handler.store = Store(args.results)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(STATIC)))
    print(f"Negotiation Lab on http://127.0.0.1:{args.port}  - research data (read-only): {args.results}"
          f"  - sandbox runs: {sandbox.SANDBOX_DIR}")
    server.serve_forever()


if __name__ == "__main__":
    main()
