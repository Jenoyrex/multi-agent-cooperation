"""A reference agent for the negotiation-lab/1 HTTP contract (stdlib only).

    python -m lab.example_http_agent [--port 8900] [--ask 0.8] [--token SECRET]

Then connect it in the Agent Lab as a Custom HTTP endpoint:
http://127.0.0.1:8900/  (use a different --port and --ask to run two).

It reads only the observation it is sent (docs/phase2-agent-lab-plan.md §4.1):
accepts an opponent offer worth at least `floor` of its own maximum, otherwise
asks for a share of every category that shrinks each round. Rule-based, not
an LLM: it ignores `instructions` and `prompt`. Replace `decide` with a call
to your own model to test it.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def decide(obs: dict, ask: float = 0.8, floor: float = 0.5) -> dict:
    me, pool, val = obs["role"], obs["resource_pool"], obs["own_valuation"]
    worth = lambda bundle: sum(bundle.get(c, 0) * v for c, v in val.items()) / 100  # noqa: E731
    standing = obs.get("standing_offer")
    if "ACCEPT" in obs["allowed_actions"] and standing and worth(standing["allocation"][me]) >= floor:
        return {"action_type": "ACCEPT", "allocation": None, "message": "That works for me."}
    if "OFFER" not in obs["allowed_actions"]:
        return {"action_type": "WALK_AWAY", "allocation": None, "message": "No acceptable offer before the deadline."}
    share = max(floor, ask - 0.05 * (obs["round"] - 1))
    return {"action_type": "OFFER", "allocation": {c: round(q * share) for c, q in pool.items()},
            "message": f"I'd like about {share:.0%} of each resource."}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8900)
    p.add_argument("--ask", type=float, default=0.8)
    p.add_argument("--token", default=None, help="require this bearer token")
    args = p.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if args.token and self.headers.get("Authorization") != f"Bearer {args.token}":
                return self._send(401, {"error": "unauthorized"})
            obs = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            self._send(200, decide(obs, ask=args.ask))

        def _send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    print(f"negotiation-lab/1 example agent on http://127.0.0.1:{args.port}/ (ask {args.ask:.0%})")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
