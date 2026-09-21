"""A deterministic, zero-cost agent used ONLY to verify the harness
end-to-end without spending API credits or requiring keys. This is not a
negotiation strategy under study — it exists purely so Phase 1's smoke
test can prove the engine works before any real model is involved.

Do not present MockAgent-vs-MockAgent runs as an experimental result
about "cooperation" anywhere in the report — they are a plumbing test.
"""
from __future__ import annotations

from src.agents.base import Agent, AgentView
from src.agents.schema import NegotiationAction
from src.environment.allocation import compute_utility


class MockAgent(Agent):
    """A simple concession-based policy, fully deterministic given the
    view it receives (no internal hidden state / no RNG at decision time):

    - Opening move: claim `opening_ask` fraction of every category
      (rounded), i.e. anchor high.
    - On later turns: look at the most recent offer on the table.
        - If it's the opponent's offer and it clears `accept_threshold`
          of this agent's own max utility (100 points), ACCEPT.
        - Otherwise, counter with a claim that concedes a bit each round
          (concession_step per round), floored at min_ask, so the
          negotiation is not guaranteed to converge — that's intentional,
          it lets the smoke test also exercise the timeout/failure path
          depending on parameters chosen.
    """

    def __init__(
        self,
        name: str = "mock-greedy",
        opening_ask: float = 0.75,
        min_ask: float = 0.35,
        concession_step: float = 0.08,
        accept_threshold: float = 0.55,
    ):
        self.name = name
        self.opening_ask = opening_ask
        self.min_ask = min_ask
        self.concession_step = concession_step
        self.accept_threshold = accept_threshold

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        last_turn = view.transcript_so_far[-1] if view.transcript_so_far else None

        # First move of the whole negotiation: open with an anchor offer.
        if last_turn is None:
            return self._make_offer(view, ask_fraction=self.opening_ask)

        # If the most recent turn was our own offer being sat on (shouldn't
        # normally happen back-to-back in an alternating protocol, but
        # handled defensively), keep pushing forward the same way.
        if last_turn.actor == view.role:
            return self._make_offer(view, ask_fraction=self._current_ask(view))

        # Most recent turn was the opponent's. If they walked away or we're
        # somehow asked to respond after an ACCEPT, just mirror WALK_AWAY —
        # protocol layer shouldn't call us again after terminal states, but
        # this keeps the agent well-defined regardless.
        if last_turn.action.action_type == "WALK_AWAY":
            return NegotiationAction(action_type="WALK_AWAY", message="Opponent walked away.")

        if last_turn.action.action_type == "OFFER":
            my_bundle_in_their_offer = self._my_bundle(view.role, last_turn.action.allocation)
            my_utility = compute_utility(my_bundle_in_their_offer, view.own_valuation)
            if (my_utility / 100.0) >= self.accept_threshold:
                return NegotiationAction(
                    action_type="ACCEPT",
                    message=f"Offer clears my threshold ({my_utility:.1f}/100).",
                )
            return self._make_offer(view, ask_fraction=self._current_ask(view))

        # Fallback (should not be reached given the three-way action_type
        # literal): treat unrecognized state as a walk-away rather than
        # crashing the session.
        return NegotiationAction(action_type="WALK_AWAY", message="Unrecognized state.")

    def _current_ask(self, view: AgentView) -> float:
        conceded = self.concession_step * (view.round_number - 1)
        return max(self.min_ask, self.opening_ask - conceded)

    def _my_bundle(self, role: str, allocation: dict | None) -> dict:
        if allocation is None:
            return {}
        # The allocation dict in a NegotiationAction is always expressed as
        # "units to Agent A" per spec's offer format; the protocol layer
        # normalizes this. MockAgent expects the normalized per-role view
        # handed to it here (see protocol.py: offers are expanded to both
        # sides before being placed in the transcript).
        return allocation.get(role, {})

    def _make_offer(self, view: AgentView, ask_fraction: float) -> NegotiationAction:
        my_role = view.role
        other_role = "B" if my_role == "A" else "A"
        my_share: dict[str, int] = {}
        other_share: dict[str, int] = {}
        for cat, qty in view.resource_pool.items():
            mine = round(qty * ask_fraction)
            mine = max(0, min(qty, mine))
            my_share[cat] = mine
            other_share[cat] = qty - mine
        allocation = {my_role: my_share, other_role: other_share}
        return NegotiationAction(
            action_type="OFFER",
            allocation=allocation,
            message=f"Proposing I take ~{ask_fraction:.0%} of each category.",
        )
