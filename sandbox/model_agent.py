"""PromptAgent: a model given the study's AgentView plus the user's persona.

It sees the public pool, its OWN values, the public transcript and the round, never the other agent's values. The
reply must be the NegotiationAction JSON. Parsing is strict (json.loads, then the frozen parse_action) and a failure
is invalid_action, never repaired. Connection failures are TransportError, which aborts that negotiation.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from lab.adapters import MAX_MESSAGE_CHARS
from sandbox import keys, policy, providers
from src.agents.base import Agent, AgentView, CallRecord
from src.agents.generation import GenerationConfig, call_with_retries
from src.agents.openai_agent import response_schema
from src.agents.parsing import parse_action
from src.agents.prompting import build_prompt, system_instructions
from src.agents.schema import InvalidAgentOutputError, NegotiationAction, TransportError

MAX_RETRIES, BACKOFF_S = 2, 1.0
MAX_ATTEMPTS = MAX_RETRIES + 1
JSON_FORMAT = ('Reply with one JSON object and nothing else: {"action_type": "OFFER" | "ACCEPT" | "WALK_AWAY", '
               '"allocation": {category: units YOU receive, for every category} or null, "message": short text or null}.')
_last_call: dict[str, float] = {}


def system_text(view: AgentView, variant: str, persona: str) -> str:
    text = system_instructions(view, variant)
    if persona:
        text += f"\n\nYour instructions from the person running this agent:\n{persona}"
    return f"{text}\n\n{JSON_FORMAT}"


def user_text(view: AgentView) -> str:
    notices = getattr(view, "notices", ())  # sandbox_v1 resource events
    return "\n".join(notices) + "\n\n" + build_prompt(view) if notices else build_prompt(view)


async def _pace(conn: dict) -> None:
    rpm = conn.get("rpm") or 0
    if rpm:
        wait = _last_call.get(conn["id"], 0.0) + 60.0 / rpm - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call[conn["id"]] = time.monotonic()


class PromptAgent(Agent):
    def __init__(self, name: str, conn: dict, model: str, variant: str, persona: str = "", temperature: Optional[float] = None,
                 max_output_tokens: int = 400, timeout_s: float = 60.0, client=None, backoff_s: float = BACKOFF_S):
        self.name, self.conn, self.model, self.variant, self.persona = name, conn, model, variant, persona
        self.temperature, self.max_output_tokens = temperature, max_output_tokens
        self.gen = GenerationConfig(model=model, temperature=temperature or 0.0, max_output_tokens=max_output_tokens,
                                    timeout_s=timeout_s, max_retries=MAX_RETRIES, retry_backoff_s=backoff_s)
        self._client = client  # stub clients in tests; otherwise built on first use, on the running event loop

    def __repr__(self):  # no key is held here, and none is ever shown
        return f"PromptAgent({self.name!r}, {self.conn['id']!r}, {self.model!r})"

    def _get_client(self):
        policy.require_local("Calling a model")
        if self._client is None:
            self._client = providers.client(self.conn, self.gen.timeout_s)
        return self._client

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            await providers._close(self._client)

    def _openai_kwargs(self, view: AgentView) -> dict:
        kw = {"model": self.model, self.conn["tokens_param"]: self.max_output_tokens,
              "messages": [{"role": "system", "content": system_text(view, self.variant, self.persona)},
                           {"role": "user", "content": user_text(view)}]}
        if self.temperature is not None:
            kw["temperature"] = self.temperature
        mode = self.conn.get("structured", "none")
        if mode == "json_schema":
            kw["response_format"] = {"type": "json_schema", "json_schema": response_schema(view.resource_pool)}
        elif mode == "json_object":
            kw["response_format"] = {"type": "json_object"}
        return kw

    def _anthropic_kwargs(self, view: AgentView) -> dict:
        # Current docs: forced tool_choice returns a 400 on the newest models, so this uses output_config.format.
        kw = {"model": self.model, "max_tokens": self.max_output_tokens, "system": system_text(view, self.variant, self.persona),
              "messages": [{"role": "user", "content": user_text(view)}],
              "output_config": {"format": {"type": "json_schema", "schema": response_schema(view.resource_pool)["schema"]}}}
        if self.temperature is not None:
            kw["extra_body"] = {"temperature": self.temperature}  # SDK 1.x has no typed argument for it
        return kw

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        self.last_call = None
        client = self._get_client()
        anthropic = self.conn["api"] == "anthropic"
        kwargs = self._anthropic_kwargs(view) if anthropic else self._openai_kwargs(view)
        create = client.messages.create if anthropic else client.chat.completions.create
        await _pace(self.conn)
        try:
            response, attempts, latency = await call_with_retries(lambda: create(**kwargs), self.gen)
        except TransportError as exc:
            detail = keys.redact(str(exc))
            self.last_call = CallRecord(model=self.model, error=detail, attempts=exc.attempts, latency_s=exc.latency_s)
            raise TransportError(detail, exc.attempts, exc.latency_s) from None
        except Exception as exc:  # noqa: BLE001 — e.g. an SDK decoding failure; never shown unredacted
            detail = keys.redact(f"{type(exc).__name__}: {exc}")
            self.last_call = CallRecord(model=self.model, error=detail)
            raise TransportError(detail) from None
        content, stop, refused, used = self._read(response, anthropic)
        self.last_call = CallRecord(model=self.model, response_model=getattr(response, "model", None), raw_output=keys.redact(content or refused or ""),
                                    stop_reason=stop, input_tokens=used[0], output_tokens=used[1], latency_s=latency, attempts=attempts)
        if stop in ("length", "max_tokens"):
            raise InvalidAgentOutputError(f"truncated output: {stop}")
        if refused or stop == "refusal":
            raise InvalidAgentOutputError("model refused")
        if not content:
            raise InvalidAgentOutputError("empty response")
        try:
            raw = json.loads(content)
            if isinstance(raw, dict) and isinstance(raw.get("message"), str) and len(raw["message"]) > MAX_MESSAGE_CHARS:
                raise InvalidAgentOutputError(f"message is longer than {MAX_MESSAGE_CHARS} characters")
            return parse_action(view, raw)
        except InvalidAgentOutputError:
            raise
        except Exception as exc:  # noqa: BLE001 — untrusted text: every failure is malformed_output
            raise InvalidAgentOutputError(f"output could not be read as an action ({type(exc).__name__})") from None

    @staticmethod
    def _read(response, anthropic: bool) -> tuple:
        """(text, stop reason, refusal, (input tokens, output tokens)). Missing fields stay None: unknown is never 0."""
        usage = getattr(response, "usage", None)
        if anthropic:
            texts = [b.text for b in getattr(response, "content", None) or [] if getattr(b, "type", None) == "text"]
            return ("".join(texts) or None, getattr(response, "stop_reason", None), None,
                    (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)))
        choice = (getattr(response, "choices", None) or [None])[0]
        msg = getattr(choice, "message", None)
        return (getattr(msg, "content", None), getattr(choice, "finish_reason", None), getattr(msg, "refusal", None),
                (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)))


def build(display_name: str, params: dict, variant: str, persona_override: Optional[str] = None) -> PromptAgent:
    """Refuses in the public demo and for a key that is missing, before anything is sent."""
    policy.require_local("Calling a model")
    conn = providers.require(params["connection"])
    return PromptAgent(display_name, conn, params["model"], variant,
                       persona=params.get("persona", "") if persona_override is None else persona_override,
                       temperature=params.get("temperature"), max_output_tokens=int(params.get("max_output_tokens", 400)),
                       timeout_s=float(params.get("timeout_s", 60)))
