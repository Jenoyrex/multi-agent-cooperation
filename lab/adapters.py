"""Agent adapters: turn a validated JSON AgentSpec into a frozen-engine Agent.

An agent is never code a visitor uploads. It is one of four kinds:

  scripted  MockAgent, the rule-based test agent (ignores instructions)
  claude    ClaudeAgent with the approved settings and the server's key
  openai    OpenAIAgent with the approved settings and the server's key
  http      an external endpoint speaking the negotiation-lab/1 contract

Every adapter subclasses src.agents.base.Agent and receives only the
AgentView the engine builds, so the privacy boundary is the Phase-1 one.
Expected failures surface as InvalidAgentOutputError (-> invalid_action) or
TransportError (-> aborted trial), exactly like the Phase-1 agents. Any
other exception is an unexpected error: the evaluation stops and the
trial is not recorded.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlparse

from dashboard import sandbox
from src.agents.base import Agent, AgentView, CallRecord
from src.agents.generation import GenerationConfig
from src.agents.mock_agent import MockAgent
from src.agents.parsing import parse_action
from src.agents.prompting import build_prompt, system_instructions
from src.agents.schema import InvalidAgentOutputError, NegotiationAction, TransportError
from src.experiments.config import APPROVED_OPERATIONAL, APPROVED_SCIENTIFIC

CONTRACT = "negotiation-lab/1"
KINDS = ("scripted", "claude", "openai", "http")
NOT_IMPLEMENTED = {"gemini": "Google Gemini", "ollama": "Ollama"}  # reachable today only through an HTTP wrapper
MAX_RESPONSE_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 1000        # a reply's public message; longer ones are malformed_output
DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S = 20.0, 60.0
LABEL_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,40}$")


class SpecError(ValueError):
    """An agent spec the lab refuses, with a message fit for the visitor."""


# ---------------------------------------------------------------- spec

def validate_agent(spec, who: str) -> tuple[dict, Optional[str]]:
    """Normalize one AgentSpec. Returns (public spec, session token or None).
    The token is split off here so nothing downstream can store or echo it."""
    if not isinstance(spec, dict):
        raise SpecError(f"{who}: choose an agent.")
    kind = spec.get("kind")
    if kind in NOT_IMPLEMENTED:
        raise SpecError(f"{who}: {NOT_IMPLEMENTED[kind]} has no native adapter yet. "
                        "Connect it through a Custom HTTP endpoint instead.")
    if kind not in KINDS:
        raise SpecError(f"{who}: unsupported agent type.")
    if kind != "http":
        return {"kind": kind}, None

    url = str(spec.get("url") or "").strip()
    u = urlparse(url)
    try:
        u.port  # raises on a malformed or out-of-range port
    except ValueError:
        u = None
    if u is None or len(url) > 500 or u.scheme not in ("http", "https") or not u.hostname:
        raise SpecError(f"{who}: the endpoint must be an http:// or https:// URL.")
    if u.username or u.password:
        raise SpecError(f"{who}: don't put credentials in the URL; use the token field, which is never stored.")
    label = str(spec.get("label") or "").strip()
    if not LABEL_RE.match(label):
        raise SpecError(f"{who}: give the agent a name of 1–40 letters, digits, spaces, dots, dashes or underscores.")
    timeout = spec.get("timeout_s", DEFAULT_TIMEOUT_S)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_S:
        raise SpecError(f"{who}: the per-turn timeout must be 1–{MAX_TIMEOUT_S:.0f} seconds.")
    token = spec.get("token") or None
    if token is not None and (not isinstance(token, str) or len(token) > 512 or not token.isprintable()):
        raise SpecError(f"{who}: the token must be printable text of at most 512 characters.")
    return {"kind": "http", "url": url, "label": label, "timeout_s": float(timeout),
            "auth": "bearer token (session only, not stored)" if token else None}, token


def missing_keys(specs) -> list[str]:
    keys = {sandbox.MODELS[s["kind"]]["key"] for s in specs if s["kind"] in sandbox.MODELS}
    return sorted(k for k in keys if not os.environ.get(k, "").strip())


def describe(spec: dict) -> dict:
    """What the report shows about an agent: never the token."""
    if spec["kind"] == "scripted":
        return {"kind": "scripted", "label": "Scripted test agent", "detail": "rule-based, ignores instructions"}
    if spec["kind"] in sandbox.MODELS:
        m = sandbox.MODELS[spec["kind"]]
        return {"kind": spec["kind"], "label": m["label"], "detail": f"{m['provider']} · {APPROVED_SCIENTIFIC[m['cls'].__name__]['model']}"}
    return {"kind": "http", "label": spec["label"], "detail": f"HTTP · {spec['url']}", "auth": spec.get("auth")}


def build_agent(spec: dict, token: Optional[str], strategy: str) -> Agent:
    if spec["kind"] == "scripted":
        return MockAgent(name="scripted")
    if spec["kind"] in sandbox.MODELS:
        cls = sandbox.MODELS[spec["kind"]]["cls"]
        return cls(GenerationConfig(**APPROVED_SCIENTIFIC[cls.__name__], **APPROVED_OPERATIONAL),
                   instructions_variant=strategy)
    return HTTPAgent(spec["url"], spec["label"], strategy, token=token, timeout_s=spec["timeout_s"])


# ---------------------------------------------------------------- observation

def observation(view: AgentView, variant: str) -> dict:
    """The negotiation-lab/1 observation. Built from the AgentView alone, so it
    cannot contain the opponent's valuation, the optimum or any seed."""
    offers = [t for t in view.transcript_so_far if t.action.action_type == "OFFER"]
    standing = offers[-1] if offers else None  # a final-round offer ends the session, so it never appears here
    final = view.rounds_remaining == 0
    can_accept = standing is not None and standing.actor != view.role
    # Informational: the engine still enforces every rule whatever the endpoint answers.
    allowed = ([] if final else ["OFFER"]) + (["ACCEPT"] if can_accept else []) + ["WALK_AWAY"]
    return {
        "contract": CONTRACT,
        "role": view.role,
        "resource_pool": dict(view.resource_pool),
        "own_valuation": dict(view.own_valuation),
        "round": view.round_number, "max_rounds": view.max_rounds,
        "rounds_remaining": view.rounds_remaining, "is_final_round": final,
        "allowed_actions": allowed,
        "standing_offer": {"by": standing.actor, "round": standing.turn_number,
                           "allocation": standing.action.allocation} if standing else None,
        "transcript": [{"round": t.turn_number, "actor": t.actor, "action": t.action.action_type,
                        "allocation": t.action.allocation, "message": t.action.message}
                       for t in view.transcript_so_far],
        "instructions": system_instructions(view, variant),
        "prompt": build_prompt(view),
    }


# ---------------------------------------------------------------- HTTP agent

# Requests run here, not in asyncio's default executor: asyncio.run() waits for the
# default executor's threads on shutdown, which let a slow endpoint stretch a turn far
# past its deadline. A bounded pool also caps how many stuck requests can pile up.
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lab-http")


def _cut(conn: http.client.HTTPConnection) -> None:
    """Deadline watchdog: shut the socket so a blocked read returns at once."""
    sock = conn.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class HTTPAgent(Agent):
    """An external agent behind a URL. The token lives only on this object,
    for this evaluation; it is sent as a bearer header and nowhere else."""

    def __init__(self, url: str, label: str, instructions_variant: str,
                 token: Optional[str] = None, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.name = f"http:{label}"
        self.url, self.instructions_variant, self.timeout_s = url, instructions_variant, timeout_s
        self._token = token

    def __repr__(self):  # never show the token, even in a traceback
        return f"HTTPAgent({self.name!r}, {self.url!r})"

    def _late(self) -> TransportError:
        return TransportError(f"no reply within {self.timeout_s:g} s")

    def _post(self, body: bytes, deadline: float) -> bytes:
        """One POST bounded by `deadline`: once connected, a watchdog shuts the
        socket at the deadline, so slow-drip headers or bodies cannot hold the
        turn open. DNS and the TLS handshake happen before the watchdog is armed;
        there the caller's wait_for ends the turn, but this thread may run on. Redirects are never followed
        (http.client does not follow them); any non-2xx is a TransportError."""
        u = urlparse(self.url)
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "negotiation-lab/1"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        left = lambda: max(0.001, deadline - time.monotonic())  # noqa: E731
        conn = (http.client.HTTPSConnection(u.hostname, u.port, timeout=left(), context=ssl.create_default_context())
                if u.scheme == "https" else http.client.HTTPConnection(u.hostname, u.port, timeout=left()))
        watchdog = None
        try:
            conn.connect()
            watchdog = threading.Timer(left(), _cut, (conn,))
            watchdog.daemon = True
            watchdog.start()
            conn.request("POST", (u.path or "/") + (f"?{u.query}" if u.query else ""), body=body, headers=headers)
            res = conn.getresponse()
            if not 200 <= res.status < 300:
                raise TransportError(f"endpoint answered HTTP {res.status}")
            data = res.read(MAX_RESPONSE_BYTES + 1)
        except TransportError:
            raise
        except (OSError, http.client.HTTPException, UnicodeError) as exc:  # UnicodeError: IDNA-invalid hostname
            if time.monotonic() >= deadline or isinstance(exc, TimeoutError):
                raise self._late() from None
            raise TransportError(f"endpoint unreachable ({type(exc).__name__})") from None
        finally:
            if watchdog:
                watchdog.cancel()
            conn.close()
        if time.monotonic() >= deadline:  # a body cut short by the watchdog is not a reply
            raise self._late()
        return data

    def _interpret(self, view: AgentView, data: bytes) -> NegotiationAction:
        """Any reply that cannot be read as an action is the frozen malformed_output,
        never an exception that escapes the engine (e.g. RecursionError from deep JSON)."""
        if len(data) > MAX_RESPONSE_BYTES:
            raise InvalidAgentOutputError(f"reply is larger than {MAX_RESPONSE_BYTES // 1024} KiB")
        try:
            reply = json.loads(data.decode("utf-8"))
            if isinstance(reply, dict) and isinstance(reply.get("message"), str) and len(reply["message"]) > MAX_MESSAGE_CHARS:
                raise InvalidAgentOutputError(f"message is longer than {MAX_MESSAGE_CHARS} characters")
            return parse_action(view, reply)
        except InvalidAgentOutputError:
            raise
        except Exception as exc:  # noqa: BLE001 — untrusted input: every failure is malformed_output
            raise InvalidAgentOutputError(f"reply could not be read as an action ({type(exc).__name__})") from None

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        body = json.dumps(observation(view, self.instructions_variant)).encode()
        start, data = time.monotonic(), None
        deadline = start + self.timeout_s
        try:
            try:
                # The watchdog ends the request at the deadline; wait_for is the backstop for a
                # request stuck where no socket exists yet (DNS) or queued behind a full pool.
                data = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(_POOL, self._post, body, deadline),
                                              self.timeout_s + 0.5)
            except asyncio.TimeoutError:
                raise self._late() from None
            action = self._interpret(view, data)
        except (TransportError, InvalidAgentOutputError) as exc:
            self.last_call = CallRecord(model=self.name, latency_s=time.monotonic() - start,
                                        raw_output=None if data is None else data.decode("utf-8", errors="replace"),
                                        error=str(exc) if isinstance(exc, TransportError) else None)
            raise
        self.last_call = CallRecord(model=self.name, raw_output=data.decode("utf-8", errors="replace"),
                                    latency_s=time.monotonic() - start)
        return action


# ---------------------------------------------------------------- connection test

PROBE_VIEW = AgentView(  # synthetic, labelled as such; not drawn from any scenario
    role="A", resource_pool={"widgets": 10, "gadgets": 10}, own_valuation={"widgets": 6.0, "gadgets": 4.0},
    transcript_so_far=[], round_number=1, rounds_remaining=9, max_rounds=10)


async def probe(spec: dict, token: Optional[str], strategy: str = "baseline_v1") -> dict:
    """Send one synthetic observation and report what came back. Starts no negotiation."""
    agent = build_agent(spec, token, strategy)
    try:
        action = await agent.generate_response(PROBE_VIEW)
    except TransportError as exc:
        return {"ok": False, "title": "Endpoint unreachable", "message": str(exc)}
    except InvalidAgentOutputError as exc:
        return {"ok": False, "title": "Reply rejected", "message": f"{exc} ({exc.reason})"}
    except Exception as exc:  # noqa: BLE001 — a probe never takes the server down with it
        return {"ok": False, "title": "Probe failed", "message": type(exc).__name__}
    call = agent.last_call
    return {"ok": True, "action": action.action_type, "allocation": action.allocation, "message": action.message,
            "latency_s": round(call.latency_s, 3) if call and call.latency_s is not None else None}
