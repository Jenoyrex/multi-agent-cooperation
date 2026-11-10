"""A fake OpenAI-compatible server on 127.0.0.1 (stdlib only): no paid call is ever made in tests.

The model name picks the behaviour. "good" plays sensibly; the others fail in one specific way.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKENS = (100, 20)


def play(user: str) -> dict:
    """Accept the other agent's offer if there is one to accept, else offer half of everything."""
    pool = dict(re.findall(r"([a-z-]+): (\d+) units", user.split("\n")[0] if user.startswith("Resource pool") else user[user.index("Resource pool"):].split("\n")[0]))
    me = re.search(r"your turn, Agent ([AB])", user)[1]
    offers = re.findall(r"Round \d+, Agent ([AB]) OFFERED", user)
    final = "FINAL round" in user
    if offers and offers[-1] != me and (final or len(offers) >= 2):
        return {"action_type": "ACCEPT", "allocation": None, "message": "Agreed."}
    if final:
        return {"action_type": "WALK_AWAY", "allocation": None, "message": "No deal."}
    return {"action_type": "OFFER", "allocation": {c: int(q) // 2 for c, q in pool.items()}, "message": "Half each."}


class FakeModels:
    def __init__(self, key_required=None):
        self.requests, self.key_required, self.fail_first = [], key_required, {"flaky": 1}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload, raw=None):
                body = raw if raw is not None else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                return self.headers.get("Authorization", "").removeprefix("Bearer ")

            def do_GET(self):
                outer.requests.append({"path": self.path, "auth": self._auth()})
                if outer.key_required and self._auth() != outer.key_required:
                    return self._send(401, {"error": {"message": f"Incorrect API key provided: {self._auth()}"}})
                self._send(200, {"object": "list", "data": [{"id": m, "object": "model", "created": 0, "owned_by": "fake"} for m in ("good", "other")]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "auth": self._auth(), "body": body})
                model = body["model"]
                if outer.key_required and self._auth() != outer.key_required:
                    return self._send(401, {"error": {"message": f"Incorrect API key provided: {self._auth()}"}})
                if model == "unauthorized":
                    return self._send(401, {"error": {"message": f"Incorrect API key provided: {self._auth()}"}})
                if model == "limited":
                    return self._send(429, {"error": {"message": "rate limited"}})
                if model == "flaky" and outer.fail_first["flaky"] > 0:
                    outer.fail_first["flaky"] -= 1
                    return self._send(429, {"error": {"message": "rate limited"}})
                if model == "slow":
                    time.sleep(3)
                if model == "not-json-http":
                    return self._send(200, None, raw=b"<html>not an api</html>")
                user = next(m["content"] for m in body["messages"] if m["role"] == "user")
                finish, content = "stop", None
                if "Resource pool" not in user:  # the connection test's tiny request
                    content = "OK"
                elif model == "garbage":
                    content = "Sure! I would like half of everything."
                elif model == "fenced":
                    content = "```json\n" + json.dumps(play(user)) + "\n```"
                elif model == "truncated":
                    finish, content = "length", '{"action_type": "OFF'
                elif model == "too-many":
                    content = json.dumps({"action_type": "OFFER", "allocation": {c: 10**6 for c in re.findall(r"([a-z-]+): \d+ units", user)}, "message": None})
                elif model == "echo-key":
                    content = json.dumps({**play(user), "message": f"my key is {self._auth()}"})
                else:
                    content = json.dumps(play(user))
                self._send(200, {"id": "x", "object": "chat.completion", "created": 0, "model": model,
                                 "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
                                 "usage": {"prompt_tokens": TOKENS[0], "completion_tokens": TOKENS[1], "total_tokens": sum(TOKENS)}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def chats(self):
        return [r for r in self.requests if r["path"].endswith("/chat/completions")]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
