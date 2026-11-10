"""The sandbox_v1 engine: the frozen alternating-offers rules, plus resource events (docs/sandbox-protocol.md).

Used only for experiments with resource events; everything else runs the frozen NegotiationSession unchanged, and a
test checks both produce the same record when there are no events. It reuses the study's validation, utility and
optimum code, and returns the study's NegotiationRecord. The private values stay on this side of the view, as in the study.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Optional

from src.agents.base import Agent, AgentView, TranscriptTurn
from src.agents.schema import InvalidAgentOutputError, NegotiationAction, TransportError
from src.environment.allocation import Allocation, InvalidAllocationError, compute_utility, validate_allocation
from src.environment.optimum import compute_optimal_welfare
from src.negotiation.protocol import NegotiationAborted, NegotiationRecord
from src.negotiation.usage import Budget, Prices, summarize_calls

PROTOCOL = "sandbox_v1"


@dataclass(frozen=True)
class SandboxView(AgentView):
    """The study's AgentView plus what both agents are told about resource events. Holds nothing private to the other agent."""
    notices: tuple = ()


def notice(ev: dict, turn: int, voided: Optional[dict]) -> str:
    void = (f" The offer Agent {voided['actor']} made in round {voided['turn']} no longer covers the full pool, so it cannot be accepted."
            if voided else "")
    extra = f" The deadline moved back by {ev['extend_rounds']} round(s)." if ev.get("extend_rounds") else ""
    return f"New resource added at the start of round {turn}: {ev['units']} units of {ev['category']}.{void}{extra} Any offer must now split the full pool."


def event_values(seed: int, ev: dict) -> dict:
    """Each agent's private points for a NEW category, drawn from its seat, the seed and the event alone."""
    return {seat: random.Random(f"{PROTOCOL}|{seed}|{ev['turn']}|{ev['category']}|{seat}").randint(0, ev["max_points"]) for seat in "AB"}


class EventSession:
    def __init__(self, agent_A: Agent, agent_B: Agent, *, seed: int, pool: dict, valuation: dict, optimal_welfare: float,
                 max_rounds: int, first_mover: str, method: str, model_A: str, model_B: str, events: list[dict],
                 run_id: Optional[str] = None, index: Optional[int] = None, budget: Optional[Budget] = None, prices: Optional[Prices] = None):
        if max_rounds < 2:
            raise ValueError("max_rounds must be >= 2 (the final turn is response-only)")
        self.agents, self.seed, self.pool = {"A": agent_A, "B": agent_B}, seed, dict(pool)
        self.val = {s: dict(valuation[s]) for s in "AB"}
        self.optimal_welfare, self.pre_event_welfare = optimal_welfare, optimal_welfare
        self.max_rounds, self.first_mover, self.method, self.models = max_rounds, first_mover, method, (model_A, model_B)
        self.events = {e["turn"]: e for e in events}
        self.run_id, self.index, self.budget, self.prices = run_id, index, budget, prices
        self.calls: list[dict] = []
        self.fired: list[dict] = []
        self.notices: list[str] = []

    def _view(self, role: str, visible: list, round_number: int) -> SandboxView:
        return SandboxView(role=role, resource_pool=dict(self.pool), own_valuation=dict(self.val[role]), transcript_so_far=list(visible),
                           round_number=round_number, rounds_remaining=self.max_rounds - round_number, max_rounds=self.max_rounds,
                           notices=tuple(self.notices))

    def _fire(self, ev: dict, turn: int, standing: Optional[TranscriptTurn]) -> None:
        new = ev["category"] not in self.pool
        points = event_values(self.seed, ev) if new else None
        if new:
            self.pool[ev["category"]] = ev["units"]
            for seat in "AB":
                self.val[seat][ev["category"]] = points[seat] / ev["units"]
        else:  # more units of a category: each unit keeps the value it already had
            self.pool[ev["category"]] += ev["units"]
        self.max_rounds += ev.get("extend_rounds", 0)
        alloc, welfare = compute_optimal_welfare(self.pool, self.val["A"], self.val["B"])
        self.optimal_welfare = welfare
        voided = {"turn": standing.turn_number, "actor": standing.actor} if standing else None
        self.notices.append(notice(ev, turn, voided))
        self.fired.append({"turn": turn, "category": ev["category"], "units": ev["units"], "new_category": new, "points": points,
                           "extend_rounds": ev.get("extend_rounds", 0), "voided_offer": voided, "pool_after": dict(self.pool),
                           "optimal_welfare_after": welfare, "optimal_allocation_after": {"A": alloc.a_A, "B": alloc.a_B}})

    def _log(self, agent: Agent, turn: int, actor: str) -> None:
        call = agent.last_call
        agent.last_call = None
        if call is not None:
            entry = {"turn_number": turn, "actor": actor, **asdict(call)}
            self.calls.append(entry)
            if self.budget:
                self.budget.add(entry)

    async def run(self) -> NegotiationRecord:
        transcript: list = []  # everything said: kept for the record and the analysis
        visible: list = []     # what agents see: the same, minus an offer an event voided
        actor, standing = self.first_mover, None
        turn = 0
        while True:
            turn += 1
            if turn in self.events:
                self._fire(self.events[turn], turn, standing)
                if standing is not None:
                    visible.remove(standing)
                standing = None
            final = turn == self.max_rounds
            agent = self.agents[actor]
            if self.budget and (why := self.budget.exceeded()):
                raise NegotiationAborted("budget_exhausted", why, turn, actor, transcript, self.calls)
            agent.last_call = None
            try:
                action = await agent.generate_response(self._view(actor, visible, turn))
            except InvalidAgentOutputError as exc:
                self._log(agent, turn, actor)
                return self._finish(transcript, "invalid_action", turn, None, exc.reason, str(exc))
            except TransportError as exc:
                self._log(agent, turn, actor)
                raise NegotiationAborted("transport_failure", str(exc), turn, actor, transcript, self.calls) from exc
            self._log(agent, turn, actor)
            record = TranscriptTurn(turn, actor, action)
            if action.action_type == "ACCEPT":
                if standing is None or standing.actor == actor:
                    return self._finish(transcript, "invalid_action", turn, None, "illegal_accept", "ACCEPT with no standing opponent offer")
                transcript.append(record)
                return self._finish(transcript, "agreed", turn, standing.action.allocation)
            if action.action_type == "WALK_AWAY":
                transcript.append(record)
                return self._finish(transcript, "walked_away", turn, None)
            if action.allocation is None:
                return self._finish(transcript, "invalid_action", turn, None, "invalid_allocation", "OFFER without allocation")
            try:
                validate_allocation(Allocation(a_A=action.allocation["A"], a_B=action.allocation["B"]), self.pool)
            except (InvalidAllocationError, KeyError, TypeError) as exc:
                return self._finish(transcript, "invalid_action", turn, None, "invalid_allocation", str(exc))
            transcript.append(record)
            if final:
                return self._finish(transcript, "timeout", turn, None)
            visible.append(record)
            standing = record
            actor = "B" if actor == "A" else "A"

    def _finish(self, transcript, outcome, turn, allocation, reason=None, detail=None) -> NegotiationRecord:
        agreed = outcome == "agreed" and allocation is not None
        u_a = compute_utility(allocation["A"], self.val["A"]) if agreed else 0.0
        u_b = compute_utility(allocation["B"], self.val["B"]) if agreed else 0.0
        return NegotiationRecord(
            instance_seed=self.seed, method=self.method, model_A=self.models[0], model_B=self.models[1], first_mover=self.first_mover,
            outcome=outcome, num_rounds=turn, final_allocation=allocation, utility_A=u_a, utility_B=u_b, total_welfare=u_a + u_b,
            optimal_welfare=self.optimal_welfare, resource_pool=dict(self.pool), valuation_A=dict(self.val["A"]), valuation_B=dict(self.val["B"]),
            transcript=transcript, invalid_reason=reason, invalid_detail=detail, first_mover_policy="explicit", run_id=self.run_id,
            negotiation_index=self.index, max_rounds=self.max_rounds, calls=list(self.calls), **summarize_calls(self.calls, self.prices))
