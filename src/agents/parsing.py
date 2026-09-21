"""Shared, repair-free translation of a model's raw structured output into a
NegotiationAction (used by every real-model agent).

Models describe an offer as "MY share per category". The protocol expects the
two-sided form, so the other side is computed as pool - my share. That is
arithmetic only: NOTHING is clamped, truncated, defaulted, or dropped. A
missing category, extra category, negative, over-large, or non-integer
quantity therefore stays wrong and is rejected downstream by the protocol's
validate_allocation (or here, for non-integers, which cannot be represented
without changing their value). A malformed output is never turned into a
different valid action.

Limitation (documented, not repair): a non-OFFER action that carries an
allocation keeps its declared action_type; the stray allocation is ignored
by the protocol (ACCEPT always refers to the standing offer). The raw output
is preserved in the call log either way.
"""
from __future__ import annotations

from typing import Any

from src.agents.base import AgentView
from src.agents.schema import InvalidAgentOutputError, NegotiationAction

ACTION_TYPES = ("OFFER", "ACCEPT", "WALK_AWAY")


def parse_action(view: AgentView, raw: Any) -> NegotiationAction:
    if not isinstance(raw, dict):
        raise InvalidAgentOutputError("model output is not a JSON object")

    action_type = raw.get("action_type")
    if action_type not in ACTION_TYPES:
        raise InvalidAgentOutputError(f"unknown action_type {action_type!r}")

    message = raw.get("message")
    if message is not None and not isinstance(message, str):
        raise InvalidAgentOutputError("message must be a string or null")

    allocation = None
    if action_type == "OFFER":
        mine = raw.get("allocation")
        # null/missing allocation is passed through as-is; the protocol
        # rejects an OFFER without allocation as invalid_allocation.
        if mine is not None:
            if not isinstance(mine, dict):
                raise InvalidAgentOutputError(
                    "allocation must be an object mapping category -> units",
                    reason="invalid_allocation",
                )
            for cat, qty in mine.items():
                # bool is an int subclass; True/False are not quantities.
                if not isinstance(qty, int) or isinstance(qty, bool):
                    raise InvalidAgentOutputError(
                        f"non-integer quantity for category {cat!r}: {qty!r}",
                        reason="invalid_allocation",
                    )
            other_role = "B" if view.role == "A" else "A"
            allocation = {
                view.role: dict(mine),
                other_role: {
                    cat: view.resource_pool[cat] - qty
                    for cat, qty in mine.items()
                    if cat in view.resource_pool
                },
            }

    return NegotiationAction(action_type=action_type, allocation=allocation, message=message)
