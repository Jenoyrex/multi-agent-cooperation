"""Read-only research dashboard server (stdlib only, localhost, GET only).

    PYTHONPATH=. python -m dashboard.server [--port 8765] [--results results]

Serves the static UI from dashboard/static and two JSON endpoints:
  GET /api/snapshot            everything except transcripts
  GET /api/negotiation?key=K   one negotiation with its annotated transcript
It never starts, modifies or reruns an experiment; the runner scripts remain
the only execution path.
"""
from __future__ import annotations

import argparse
import json
import math
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dashboard.data import RESULTS_DIR, Store

STATIC = Path(__file__).resolve().parent / "static"


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

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/snapshot":
            return self._json(self.store.snapshot())
        if url.path == "/api/negotiation":
            detail = self.store.negotiation(parse_qs(url.query).get("key", [""])[0])
            return self._json(detail) if detail else self._json({"error": "not found"}, 404)
        if url.path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

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
    print(f"Dashboard (read-only) on http://127.0.0.1:{args.port}  - data: {args.results}")
    server.serve_forever()


if __name__ == "__main__":
    main()
