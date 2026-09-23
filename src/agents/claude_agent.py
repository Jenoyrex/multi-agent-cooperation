"""Claude negotiating agent. Requires ANTHROPIC_API_KEY in the environment
(loaded from .env by the caller). Generation settings (model, temperature,
max output tokens, timeout, max retries) are explicit — see
src/agents/generation.py; there are no defaults here.

SDK-level retries are disabled (max_retries=0): retries are done by
call_with_retries so the attempt count is observable and recorded.
"""
from __future__ import annotations

import json

from src.agents.base import Agent, AgentView, CallRecord
from src.agents.generation import GenerationConfig, call_with_retries
from src.agents.parsing import parse_action
from src.agents.prompting import BASELINE_VARIANT, build_prompt, check_variant, system_instructions
from src.agents.schema import InvalidAgentOutputError, NegotiationAction, TransportError, allocation_json_schema
from src.environment.resources import DEFAULT_CATEGORY_NAMES

ALLOCATION_DESCRIPTION = (
    "Required if action_type is OFFER. Maps each category name to "
    "the number of units YOU would receive; the rest of that "
    "category's units go to the other agent."
)


def action_tool(categories) -> dict:
    """The forced tool, with the allocation keyed by this pool's categories."""
    return {
        "name": "submit_negotiation_action",
        "description": "Submit your action for this negotiation turn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "enum": ["OFFER", "ACCEPT", "WALK_AWAY"]},
                "allocation": allocation_json_schema(categories, ALLOCATION_DESCRIPTION),
                "message": {"type": ["string", "null"]},
            },
            "required": ["action_type"],
        },
    }


# The tool as sent for the default pool; fingerprinted by provenance.prompt_hash.
ACTION_TOOL = action_tool(DEFAULT_CATEGORY_NAMES)


class ClaudeAgent(Agent):
    def __init__(self, config: GenerationConfig, name: str | None = None, client=None,
                 instructions_variant: str = BASELINE_VARIANT):
        self.config = config
        self.name = name or f"claude:{config.model}"
        self._client = client  # injectable for stub-client tests
        # Which instruction strategy this agent sends; recorded in run config.
        self.instructions_variant = check_variant(instructions_variant)

    def _get_client(self):
        if self._client is None:
            import anthropic  # local import: keeps this optional for mock-only runs
            self._client = anthropic.AsyncAnthropic(  # reads ANTHROPIC_API_KEY from env
                timeout=self.config.timeout_s, max_retries=0,
            )
        return self._client

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        cfg = self.config
        self.last_call = None
        client = self._get_client()
        kwargs = dict(
            model=cfg.model,
            max_tokens=cfg.max_output_tokens,
            temperature=cfg.temperature,
            system=system_instructions(view, self.instructions_variant),
            messages=[{"role": "user", "content": build_prompt(view)}],
            tools=[action_tool(view.resource_pool)],
            tool_choice={"type": "tool", "name": "submit_negotiation_action"},
        )
        try:
            response, attempts, latency = await call_with_retries(
                lambda: client.messages.create(**kwargs), cfg
            )
        except TransportError as exc:
            self.last_call = CallRecord(
                model=cfg.model, error=str(exc), attempts=exc.attempts, latency_s=exc.latency_s
            )
            raise

        usage = getattr(response, "usage", None)
        tool_block = next(
            (b for b in response.content
             if getattr(b, "type", None) == "tool_use" and b.name == "submit_negotiation_action"),
            None,
        )
        self.last_call = CallRecord(
            model=cfg.model,
            response_model=getattr(response, "model", None),
            raw_output=_raw_output(response, tool_block),
            stop_reason=getattr(response, "stop_reason", None),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            latency_s=latency,
            attempts=attempts,
        )

        if self.last_call.stop_reason == "max_tokens":
            raise InvalidAgentOutputError("truncated output: stop_reason=max_tokens")
        if tool_block is None:
            raise InvalidAgentOutputError("empty or refused response: no tool_use block")
        return parse_action(view, tool_block.input)


def _raw_output(response, tool_block) -> str | None:
    if tool_block is not None:
        try:
            return json.dumps(tool_block.input)
        except (TypeError, ValueError):
            return repr(tool_block.input)
    texts = [getattr(b, "text", None) for b in response.content]
    texts = [t for t in texts if t]
    return "\n".join(texts) if texts else None
