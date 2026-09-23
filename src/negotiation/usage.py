"""Token/latency/cost accounting and the hard run budget.

Prices are never hardcoded: callers supply {model_id: (usd_per_1M_input,
usd_per_1M_output)}. Unknown is never turned into zero or a partial sum:
a token/cost total is None if there are no calls, or if ANY call that
completed lacks that figure (provider reported no usage, or no price for
the model). Calls that failed outright (`error` set) and retry attempts
that were discarded consumed tokens that are unobservable; they are
excluded from token/cost totals (their latency and attempts are counted).
The Budget treats unknown usage as 0 because it cannot enforce what it
cannot see.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

Prices = Dict[str, Tuple[float, float]]


def call_cost(call: dict, prices: Optional[Prices]) -> Optional[float]:
    price = prices.get(call.get("model")) if prices else None
    if price is None or call.get("input_tokens") is None or call.get("output_tokens") is None:
        return None
    return (call["input_tokens"] * price[0] + call["output_tokens"] * price[1]) / 1_000_000


def _total(values: Iterable[Optional[float]]):
    """Sum, or None if there are no values or any value is unknown."""
    values = list(values)
    return None if not values or any(v is None for v in values) else sum(values)


def summarize_calls(calls: Iterable[dict], prices: Optional[Prices] = None) -> dict:
    """Aggregate per-call dicts (as logged on a record) into totals."""
    calls = list(calls)
    completed = [c for c in calls if not c.get("error")]
    return {
        "api_calls": len(calls),
        "api_attempts": sum(c.get("attempts") or 1 for c in calls),
        "input_tokens": _total(c.get("input_tokens") for c in completed),
        "output_tokens": _total(c.get("output_tokens") for c in completed),
        "latency_s": _total(c.get("latency_s") for c in calls),
        "cost_usd": _total(call_cost(c, prices) for c in completed),
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
