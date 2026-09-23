"""Structured negotiation action schema. See docs/spec.md §3.5.

Every agent implementation (mock, Claude, OpenAI, future ones) must return
exactly this shape. The negotiation protocol only ever talks to agents
through this schema — it has no model-specific knowledge.
"""
from __future__ import annotations

from typing import Dict, Iterable, Literal, Optional

from pydantic import BaseModel, StrictInt, field_validator


def allocation_json_schema(categories: Iterable[str], description: str) -> dict:
    """JSON Schema for a model's single-sided "my units per category" offer,
    shared by every real-model agent. Each pool category is a required
    integer property and no other keys are allowed: OpenAI strict structured
    output rejects an object schema without `additionalProperties: false`,
    and with it but no listed properties it could only ever produce `{}`.
    Nullable (ACCEPT / WALK_AWAY) via anyOf. The schema does not bound
    quantities: range and feasibility stay the protocol's job (no repair)."""
    categories = list(categories)
    return {
        "anyOf": [
            {
                "type": "object",
                "description": description,
                "properties": {c: {"type": "integer"} for c in categories},
                "required": categories,
                "additionalProperties": False,
            },
            {"type": "null"},
        ]
    }


class NegotiationAction(BaseModel):
    action_type: Literal["OFFER", "ACCEPT", "WALK_AWAY"]
    # Two-sided form: {"A": {category: units, ...}, "B": {category: units, ...}}.
    # Individual agent implementations receive/produce a model's raw
    # single-sided "my share" output and translate it to this two-sided form
    # (see src/agents/parsing.py) WITHOUT repairing it: the translation is
    # pure arithmetic (other side = pool - my share), so anything infeasible
    # stays infeasible and is rejected by the protocol's allocation
    # validator. The protocol and storage layers only ever deal with this
    # two-sided shape.
    allocation: Optional[Dict[str, Dict[str, StrictInt]]] = None
    message: Optional[str] = None

    @field_validator("allocation")
    @classmethod
    def offer_requires_allocation(cls, v, info):
        # Full cross-field validation (action_type == OFFER => allocation
        # required and complete) happens in the protocol layer, which has
        # the resource pool available to validate against. This validator
        # only catches the cheap, local case.
        return v


class InvalidAgentOutputError(ValueError):
    """Raised when an agent's output cannot be turned into a usable
    NegotiationAction (not schema-valid, truncated, refused, empty, or a
    structurally invalid allocation). Per spec §6 the protocol handles this
    safely: the negotiation ends as `invalid_action` and is never retried.

    `reason` is the machine-readable invalid_reason recorded on the
    negotiation: "malformed_output" (default) or "invalid_allocation"."""

    def __init__(self, message: str, reason: str = "malformed_output"):
        super().__init__(message)
        self.reason = reason


class TransportError(Exception):
    """An API/transport/infrastructure failure (connection error, timeout,
    rate limit or 5xx after retries were exhausted, auth/bad-request).
    This is NOT a strategic action and never maps to any of the four
    negotiation outcomes: the protocol aborts the session (see
    NegotiationAborted) and the runner records it separately."""

    def __init__(self, message: str, attempts: int = 1, latency_s: float = 0.0):
        super().__init__(message)
        self.attempts = attempts
        self.latency_s = latency_s
