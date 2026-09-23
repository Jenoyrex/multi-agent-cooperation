"""Common agent interface. See docs/spec.md §3.4, §3.5, Agent Abstraction.

The negotiation protocol depends only on this interface — it never checks
"is this a Claude agent" or "is this an OpenAI agent" anywhere in its logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.agents.schema import NegotiationAction


@dataclass(frozen=True)
class TranscriptTurn:
    """One publicly-visible turn in the negotiation. Contains no private
    valuation data for either side — this is exactly what gets shown to
    both agents (spec §3.4)."""
    turn_number: int
    actor: str  # "A" or "B"
    action: NegotiationAction


@dataclass(frozen=True)
class AgentView:
    """Everything one agent is allowed to see. Built by the protocol layer
    from a strictly larger EvaluatorState (src/negotiation/protocol.py) —
    the opponent's valuation, the ground-truth optimum, and any hidden
    metrics simply do not exist as fields on this object. See spec §3.4
    and the isolation test in tests/test_privacy_isolation.py.
    """
    role: str  # "A" or "B"
    resource_pool: Dict[str, int]
    own_valuation: Dict[str, float]
    transcript_so_far: List[TranscriptTurn]
    round_number: int
    rounds_remaining: int
    max_rounds: int


@dataclass(frozen=True)
class CallRecord:
    """Evaluator-side diagnostics for ONE agent turn (one logical API call,
    including any retries). Never shown to agents. `raw_output` is exactly
    what the model returned, before any parsing."""
    model: Optional[str] = None           # requested model id
    response_model: Optional[str] = None  # model id the API reports back
    raw_output: Optional[str] = None
    stop_reason: Optional[str] = None
    input_tokens: Optional[int] = None    # None = provider did not report
    output_tokens: Optional[int] = None
    latency_s: Optional[float] = None     # wall time across all attempts
    attempts: int = 1                     # 1 = no retries were needed
    error: Optional[str] = None           # set when the call failed (TransportError)


class Agent(ABC):
    """Base class for any negotiating agent (mock or real model)."""

    name: str  # e.g. "mock-greedy", "claude-sonnet-4-6", "gpt-4o"
    # Generation settings (real agents only; None for mocks).
    config = None
    # Set by real agents after every generate_response call (success or
    # failure); the protocol reads and clears it. None for mock agents.
    last_call: Optional[CallRecord] = None

    @abstractmethod
    async def generate_response(self, view: AgentView) -> NegotiationAction:
        """Given everything this agent is allowed to see, return exactly
        one structured NegotiationAction. Implementations are responsible
        for their own prompt construction and structured-output parsing;
        callers only ever see the parsed NegotiationAction, an
        InvalidAgentOutputError (bad model output), or a TransportError
        (infrastructure failure)."""
        raise NotImplementedError
