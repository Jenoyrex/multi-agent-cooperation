"""Starter template for a Cooperation Lab agent.

Upload this file on the Agents page to check it against the engine's interface.
The lab reads (parses) uploaded files and never executes them. To actually run an
agent of your own, serve it on this machine and add it as a "Local HTTP agent"
(see lab/example_http_agent.py).

What your agent receives each turn (`view`, an AgentView):
    view.role               "A" or "B": your seat
    view.resource_pool      {"food": 20, ...}: units of each resource
    view.own_valuation      {"food": 2.5, ...}: YOUR private value per unit (others' are hidden)
    view.transcript_so_far  earlier turns; each has .actor and .action
    view.round_number       this turn, starting at 1
    view.max_rounds         the deadline

What it must return: a NegotiationAction with action_type
    "OFFER"      plus an allocation for both seats that uses every unit exactly once,
    "ACCEPT"     the other agent's standing offer, or
    "WALK_AWAY"  to end without agreement.
"""
from src.agents.base import Agent, AgentView
from src.agents.schema import NegotiationAction


class MyCustomAgent(Agent):
    """Asks for the resources it values most, then accepts any offer worth at least half its points."""

    def __init__(self, name: str = "My custom agent"):
        self.name, self.config = name, None

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        other = "B" if view.role == "A" else "A"
        last = view.transcript_so_far[-1] if view.transcript_so_far else None

        # 1. Accept a standing offer from the other agent if it is worth enough to us.
        if last and last.actor != view.role and last.action.action_type == "OFFER":
            mine = last.action.allocation[view.role]
            worth = sum(mine[c] * view.own_valuation[c] for c in view.resource_pool)
            if worth >= 50:
                return NegotiationAction(action_type="ACCEPT", message=f"Accepting: worth {worth:.0f} of my 100 points.")

        # 2. At the deadline, stop rather than make an offer nobody can answer.
        if view.round_number == view.max_rounds:
            return NegotiationAction(action_type="WALK_AWAY", message="No acceptable offer before the deadline.")

        # 3. Otherwise offer: 80% of what we value most, 30% of everything else.
        best = max(view.resource_pool, key=lambda c: view.own_valuation[c] * view.resource_pool[c])
        mine = {c: round(q * (0.8 if c == best else 0.3)) for c, q in view.resource_pool.items()}
        theirs = {c: q - mine[c] for c, q in view.resource_pool.items()}
        return NegotiationAction(action_type="OFFER", allocation={view.role: mine, other: theirs},
                                 message=f"I mostly need {best}.")
