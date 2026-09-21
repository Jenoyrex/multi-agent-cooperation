"""Token/latency/cost accounting and the hard run budget.

Prices are never hardcoded: callers supply {model_id: (usd_per_1M_input,
usd_per_1M_output)}. A call whose model has no price, or whose provider did
not report tokens, contributes no cost / no tokens (unknown != zero, so
sums are None when nothing was reported). Tokens consumed by retry attempts
that were discarded, and by calls that failed outright, are not observable
and therefore not counted.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

Prices = Dict[str, Tuple[float, float]]


def call_cost(call: dict, prices: Optional[Prices]) -> Optional[float]:
    price = prices.get(call.get("model")) if prices else None
    if price is None or call.get("input_tokens") is None or call.get("output_tokens") is None:
        return None
    return (call["input_tokens"] * price[0] + call["output_tokens"] * price[1]) / 1_000_000


def _sum(values: Iterable[Optional[float]]):
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def summarize_calls(calls: Iterable[dict], prices: Optional[Prices] = None) -> dict:
    """Aggregate per-call dicts (as logged on a record) into totals."""
    calls = list(calls)
    return {
        "api_calls": len(calls),
        "api_attempts": sum(c.get("attempts") or 1 for c in calls),
        "input_tokens": _sum(c.get("input_tokens") for c in calls),
        "output_tokens": _sum(c.get("output_tokens") for c in calls),
        "latency_s": _sum(c.get("latency_s") for c in calls),
        "cost_usd": _sum(call_cost(c, prices) for c in calls),
    }


class Budget:
    """Hard cap on total tokens and/or dollars for a run. Checked BEFORE
    every agent call, so no new call starts once a cap is reached. One call
    can overshoot the cap by at most its own usage (bounded by that call's
    input size plus max_output_tokens)."""

    def __init__(
        self,
        max_total_tokens: Optional[int] = None,
        max_cost_usd: Optional[float] = None,
        prices: Optional[Prices] = None,
    ):
        self.max_total_tokens = max_total_tokens
        self.max_cost_usd = max_cost_usd
        self.prices = prices
        self.spent_tokens = 0
        self.spent_cost_usd = 0.0

    def add(self, call: dict) -> None:
        self.spent_tokens += (call.get("input_tokens") or 0) + (call.get("output_tokens") or 0)
        self.spent_cost_usd += call_cost(call, self.prices) or 0.0

    def exceeded(self) -> Optional[str]:
        if self.max_total_tokens is not None and self.spent_tokens >= self.max_total_tokens:
            return f"token budget reached: {self.spent_tokens} >= {self.max_total_tokens}"
        if self.max_cost_usd is not None and self.spent_cost_usd >= self.max_cost_usd:
            return f"cost budget reached: ${self.spent_cost_usd:.4f} >= ${self.max_cost_usd:.4f}"
        return None
