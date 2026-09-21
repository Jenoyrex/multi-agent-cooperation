"""The negotiation protocol engine. See docs/spec.md §3.

This module owns the privacy boundary described in spec §3.4: it holds an
EvaluatorState (everything, including both hidden valuations and the
precomputed optimum) and only ever constructs the much smaller AgentView
objects to pass to agents. Agent code never receives an EvaluatorState.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from src.agents.base import Agent, AgentView, TranscriptTurn
from src.agents.schema import InvalidAgentOutputError, NegotiationAction, TransportError
from src.environment.allocation import Allocation, InvalidAllocationError, compute_utility, validate_allocation
from src.negotiation.usage import Budget, Prices, summarize_calls

Outcome = Literal["agreed", "walked_away", "timeout", "invalid_action"]
# Machine-readable cause, set only when outcome == "invalid_action".
InvalidReason = Literal[
    "malformed_output",    # agent output could not be parsed into an action
    "invalid_allocation",  # OFFER with missing or infeasible allocation
    "illegal_accept",      # ACCEPT with no standing opponent offer
]
# Why a session was aborted WITHOUT a strategic outcome.
AbortReason = Literal["transport_failure", "budget_exhausted"]


@dataclass(frozen=True)
class EvaluatorState:
    """Evaluator-only state. NEVER passed to agent code — see
    tests/test_privacy_isolation.py, which asserts this structurally."""
    instance_seed: int
    resource_pool: Dict[str, int]
    valuation_A: Dict[str, float]
    valuation_B: Dict[str, float]
    optimal_allocation: Allocation
    optimal_welfare: float
    max_rounds: int
    first_mover: str  # "A" or "B"
    method: str
    model_A_name: str
    model_B_name: str
    # How first_mover was chosen; independent of instance_seed (see runner).
    first_mover_policy: str = "explicit"
    # Provenance: which run this negotiation belongs to and its index in it.
    run_id: Optional[str] = None
    negotiation_index: Optional[int] = None


@dataclass
class NegotiationRecord:
    instance_seed: int
    method: str
    model_A: str
    model_B: str
    first_mover: str
    outcome: Outcome
    num_rounds: int
    final_allocation: Optional[dict]
    utility_A: float
    utility_B: float
    total_welfare: float
    optimal_welfare: float
    # Evaluator-side context, retained for storage/analysis. Never handed
    # to agent code — this is a record produced AFTER the session ends.
    resource_pool: Dict[str, int] = field(default_factory=dict)
    valuation_A: Dict[str, float] = field(default_factory=dict)
    valuation_B: Dict[str, float] = field(default_factory=dict)
    transcript: List[TranscriptTurn] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    invalid_reason: Optional[InvalidReason] = None
    invalid_detail: Optional[str] = None  # free-text diagnostic for invalid_action
    first_mover_policy: str = "explicit"
    run_id: Optional[str] = None
    negotiation_index: Optional[int] = None
    max_rounds: Optional[int] = None
    # One dict per real-model turn (turn_number, actor, raw_output, tokens,
    # latency, attempts, ...) — including turns that ended in invalid_action.
    calls: List[dict] = field(default_factory=list)
    # Aggregates over `calls` (None = provider reported nothing).
    api_calls: int = 0
    api_attempts: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_s: Optional[float] = None
    cost_usd: Optional[float] = None


class NegotiationAborted(Exception):
    """The session could not complete for infrastructure reasons (transport
    failure or run budget). NOT a strategic outcome: no NegotiationRecord is
    produced, and the negotiation must not be resumed — the runner re-runs
    it from scratch if configured to."""

    def __init__(self, reason: AbortReason, detail: str, round_number: int, actor: str,
                 transcript: List[TranscriptTurn], calls: List[dict]):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.round_number = round_number
        self.actor = actor
        self.transcript = transcript
        self.calls = calls


class NegotiationSession:
    def __init__(
        self,
        agent_A: Agent,
        agent_B: Agent,
        state: EvaluatorState,
        budget: Optional[Budget] = None,
        prices: Optional[Prices] = None,
    ):
        self.agents = {"A": agent_A, "B": agent_B}
        self.state = state
        self.budget = budget
        self.prices = prices
        self._calls: List[dict] = []

    def _build_view(self, role: str, transcript: List[TranscriptTurn], round_number: int) -> AgentView:
        valuation = self.state.valuation_A if role == "A" else self.state.valuation_B
        return AgentView(
            role=role,
            resource_pool=dict(self.state.resource_pool),
            own_valuation=dict(valuation),
            transcript_so_far=list(transcript),
            round_number=round_number,
            rounds_remaining=self.state.max_rounds - round_number,
            max_rounds=self.state.max_rounds,
        )

    def _invalid(self, transcript, round_number: int, reason: InvalidReason,
                 detail: Optional[str] = None) -> NegotiationRecord:
        return self._finalize(
            transcript, outcome="invalid_action", num_rounds=round_number,
            final_allocation=None, invalid_reason=reason, invalid_detail=detail,
        )

    def _log_call(self, agent: Agent, round_number: int, actor: str) -> None:
        call = agent.last_call
        agent.last_call = None
        if call is None:  # mock agents make no API calls
            return
        entry = {"turn_number": round_number, "actor": actor, **dataclasses.asdict(call)}
        self._calls.append(entry)
        if self.budget:
            self.budget.add(entry)

    async def run(self) -> NegotiationRecord:
        transcript: List[TranscriptTurn] = []
        self._calls = []
        actor = self.state.first_mover
        last_offer_on_table: Optional[NegotiationAction] = None
        last_offer_actor: Optional[str] = None

        for round_number in range(1, self.state.max_rounds + 1):
            # The final turn is the deadline: it can only ACCEPT or WALK_AWAY.
            # A (well-formed) OFFER there can never be answered -> timeout.
            is_final_turn = round_number == self.state.max_rounds
            view = self._build_view(actor, transcript, round_number)
            agent = self.agents[actor]

            if self.budget and (why := self.budget.exceeded()):
                raise NegotiationAborted("budget_exhausted", why, round_number, actor,
                                         transcript, self._calls)

            agent.last_call = None
            try:
                action = await agent.generate_response(view)
            except InvalidAgentOutputError as exc:
                self._log_call(agent, round_number, actor)
                return self._invalid(transcript, round_number, exc.reason, str(exc))
            except TransportError as exc:
                self._log_call(agent, round_number, actor)
                raise NegotiationAborted("transport_failure", str(exc), round_number, actor,
                                         transcript, self._calls) from exc
            self._log_call(agent, round_number, actor)

            # Protocol-level validation beyond the pydantic schema.
            if action.action_type == "ACCEPT":
                if last_offer_on_table is None or last_offer_actor == actor:
                    # Can't accept nothing, and can't "accept" your own
                    # standing offer via this action (spec §3.2).
                    return self._invalid(transcript, round_number, "illegal_accept",
                                         "ACCEPT with no standing opponent offer")
                transcript.append(TranscriptTurn(round_number, actor, action))
                return self._finalize(
                    transcript, outcome="agreed", num_rounds=round_number,
                    final_allocation=last_offer_on_table.allocation,
                )

            if action.action_type == "WALK_AWAY":
                transcript.append(TranscriptTurn(round_number, actor, action))
                return self._finalize(
                    transcript, outcome="walked_away", num_rounds=round_number,
                    final_allocation=None,
                )

            if action.action_type == "OFFER":
                if action.allocation is None:
                    return self._invalid(transcript, round_number, "invalid_allocation",
                                         "OFFER without allocation")
                try:
                    alloc = Allocation(a_A=action.allocation["A"], a_B=action.allocation["B"])
                    validate_allocation(alloc, self.state.resource_pool)
                except (InvalidAllocationError, KeyError, TypeError) as exc:
                    return self._invalid(transcript, round_number, "invalid_allocation", str(exc))
                transcript.append(TranscriptTurn(round_number, actor, action))
                if is_final_turn:
                    # Deadline reached without agreement. The offer stays in
                    # the transcript for diagnostics but is never standing.
                    return self._finalize(
                        transcript, outcome="timeout", num_rounds=round_number,
                        final_allocation=None,
                    )
                last_offer_on_table = action
                last_offer_actor = actor

            actor = "B" if actor == "A" else "A"

        # Defensive only: the final turn always terminates above, so this is
        # unreachable with max_rounds >= 1.
        return self._finalize(
            transcript, outcome="timeout", num_rounds=self.state.max_rounds,
            final_allocation=None,
        )

    def _finalize(
        self,
        transcript: List[TranscriptTurn],
        outcome: Outcome,
        num_rounds: int,
        final_allocation: Optional[dict],
        invalid_reason: Optional[InvalidReason] = None,
        invalid_detail: Optional[str] = None,
    ) -> NegotiationRecord:
        if outcome == "agreed" and final_allocation is not None:
            utility_A = compute_utility(final_allocation["A"], self.state.valuation_A)
            utility_B = compute_utility(final_allocation["B"], self.state.valuation_B)
        else:
            utility_A = 0.0
            utility_B = 0.0

        usage = summarize_calls(self._calls, self.prices)
        return NegotiationRecord(
            instance_seed=self.state.instance_seed,
            method=self.state.method,
            model_A=self.state.model_A_name,
            model_B=self.state.model_B_name,
            first_mover=self.state.first_mover,
            outcome=outcome,
            num_rounds=num_rounds,
            final_allocation=final_allocation,
            utility_A=utility_A,
            utility_B=utility_B,
            total_welfare=utility_A + utility_B,
            optimal_welfare=self.state.optimal_welfare,
            resource_pool=dict(self.state.resource_pool),
            valuation_A=dict(self.state.valuation_A),
            valuation_B=dict(self.state.valuation_B),
            transcript=transcript,
            invalid_reason=invalid_reason,
            invalid_detail=invalid_detail,
            first_mover_policy=self.state.first_mover_policy,
            run_id=self.state.run_id,
            negotiation_index=self.state.negotiation_index,
            max_rounds=self.state.max_rounds,
            calls=list(self._calls),
            **usage,
        )
