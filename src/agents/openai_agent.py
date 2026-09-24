"""OpenAI (GPT) negotiating agent. Requires OPENAI_API_KEY in the
environment. Generation settings (model, temperature, max output tokens,
timeout, max retries) are explicit — see src/agents/generation.py; there are
no defaults here.

SDK-level retries are disabled (max_retries=0): retries are done by
call_with_retries so the attempt count is observable and recorded.
"""
from __future__ import annotations

import json

from src.agents.base import Agent, AgentView, CallRecord
from src.agents.generation import ConfigError, GenerationConfig, call_with_retries
from src.agents.parsing import parse_action
from src.agents.prompting import BASELINE_VARIANT, build_prompt, check_variant, system_instructions
from src.agents.schema import InvalidAgentOutputError, NegotiationAction, TransportError, allocation_json_schema
from src.environment.resources import DEFAULT_CATEGORY_NAMES

ALLOCATION_DESCRIPTION = (
    "Required if action_type is OFFER. Maps each category name to "
    "the number of units YOU would receive."
)


def response_schema(categories) -> dict:
    """Strict structured-output schema, with the allocation keyed by this
    pool's categories (strict mode needs every object's keys listed)."""
    return {
        "name": "negotiation_action",
        "schema": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "enum": ["OFFER", "ACCEPT", "WALK_AWAY"]},
                "allocation": allocation_json_schema(categories, ALLOCATION_DESCRIPTION),
                "message": {"type": ["string", "null"]},
            },
            "required": ["action_type", "allocation", "message"],
            "additionalProperties": False,
        },
        "strict": True,
    }


# The schema as sent for the default pool; fingerprinted by provenance.prompt_hash.
RESPONSE_SCHEMA = response_schema(DEFAULT_CATEGORY_NAMES)


class OpenAIAgent(Agent):
    def __init__(self, config: GenerationConfig, name: str | None = None, client=None,
                 instructions_variant: str = BASELINE_VARIANT):
        self.config = config
        self.name = name or f"openai:{config.model}"
        self._client = client  # injectable for stub-client tests
        # Which instruction strategy this agent sends; recorded in run config.
        self.instructions_variant = check_variant(instructions_variant)
        # Non-reasoning model: no reasoning_effort is ever sent (spec §8.15).
        if config.effort is not None:
            raise ConfigError("OpenAIAgent sends no reasoning effort; effort must be None")
        self.api_seed = None  # no `seed` is sent

    def _get_client(self):
        if self._client is None:
            import openai  # local import: keeps this optional for mock-only runs
            self._client = openai.AsyncOpenAI(  # reads OPENAI_API_KEY from env
                timeout=self.config.timeout_s, max_retries=0,
            )
        return self._client

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        cfg = self.config
        self.last_call = None
        client = self._get_client()
        kwargs = dict(
            model=cfg.model,
            temperature=cfg.temperature,
            max_completion_tokens=cfg.max_output_tokens,
            messages=[
                {"role": "system", "content": system_instructions(view, self.instructions_variant)},
                {"role": "user", "content": build_prompt(view)},
            ],
            response_format={"type": "json_schema", "json_schema": response_schema(view.resource_pool)},
        )
        try:
            response, attempts, latency = await call_with_retries(
                lambda: client.chat.completions.create(**kwargs), cfg
            )
        except TransportError as exc:
            self.last_call = CallRecord(
                model=cfg.model, error=str(exc), attempts=exc.attempts, latency_s=exc.latency_s
            )
            raise

        usage = getattr(response, "usage", None)
        choice = response.choices[0] if response.choices else None
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        refusal = getattr(message, "refusal", None)
        self.last_call = CallRecord(
            model=cfg.model,
            response_model=getattr(response, "model", None),
            raw_output=content if content else refusal,
            stop_reason=getattr(choice, "finish_reason", None),
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            latency_s=latency,
            attempts=attempts,
        )

        if self.last_call.stop_reason == "length":
            raise InvalidAgentOutputError("truncated output: finish_reason=length")
        if refusal:
            raise InvalidAgentOutputError("model refused")
        if not content:
            raise InvalidAgentOutputError("empty response")
        try:
            raw = json.loads(content)
        except ValueError as exc:
            raise InvalidAgentOutputError(f"output is not valid JSON: {exc}") from exc
        return parse_action(view, raw)
