"""Token/latency/cost accounting and the hard run budget.

Prices are never hardcoded: callers supply {model_id: (usd_per_1M_input,
usd_per_1M_output)}. Unknown is never turned into zero or a partial sum:
a token/cost total is None if there are no calls, or if ANY call that
completed lacks that figure (provider reported no usage, or no price for
the model). Calls that failed outright (`error` set) and retry attempts
that were discarded consumed tokens that are unobservable; they are
excluded from token/cost totals (their latency and attempts are counted).
The Budget never treats unknown usage as 0 (see Budget).
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


# model -> (max input tokens per attempt, max output tokens, max attempts per call)
Reserve = Dict[str, Tuple[int, int, int]]


class Budget:
    """Hard cap on total tokens and/or dollars, checked BEFORE every agent
    call. One Budget may be shared by several runs (the pilot shares one
    across its 8 batches).

    Without `reserve`, no new call starts once a cap is reached, and one call
    can overshoot by its own usage. With `reserve`, a call starts only if the
    worst case of the next call still fits: max_attempts * (max_input +
    max_output) tokens, and that at its price, for the most expensive
    reserved model. max_input is an explicit ceiling (input size is only
    known after the call), so a call reporting more input tokens than the
    ceiling stops all further calls.

    Unknown usage is never counted as 0. Each attempt whose usage is unknown
    (a failed call, a discarded retry, a completed call without reported
    usage) is charged the model's worst case per attempt; with no reserve
    entry for the model, unknown usage stops all further calls, as does an
    unknown cost under a dollar cap."""

    def __init__(
        self,
        max_total_tokens: Optional[int] = None,
        max_cost_usd: Optional[float] = None,
        prices: Optional[Prices] = None,
        reserve: Optional[Reserve] = None,
    ):
        self.max_total_tokens = max_total_tokens
        self.max_cost_usd = max_cost_usd
        self.prices = prices
        self.reserve = reserve or {}
        if max_cost_usd is not None:
            missing = sorted(m for m in self.reserve if not prices or m not in prices)
            if missing:
                raise ValueError(f"budget_max_cost_usd needs prices for models: {missing}")
        self.spent_tokens = 0
        self.spent_cost_usd = 0.0
        self.stop_reason: Optional[str] = None

    def _attempt_worst_case(self, model: str) -> Tuple[int, Optional[float]]:
        max_in, max_out, _ = self.reserve[model]
        cost = call_cost({"model": model, "input_tokens": max_in, "output_tokens": max_out}, self.prices)
        return max_in + max_out, cost

    def next_call_reserve(self) -> Tuple[int, float]:
        """Worst-case (tokens, dollars) of the next call, over all reserved
        models; (0, 0.0) without a reserve."""
        tokens, cost = 0, 0.0
        for model, (_, _, attempts) in self.reserve.items():
            t, c = self._attempt_worst_case(model)
            tokens, cost = max(tokens, attempts * t), max(cost, attempts * (c or 0.0))
        return tokens, cost

    def add(self, call: dict) -> None:
        model = call.get("model")
        attempts = call.get("attempts") or 1
        known = (not call.get("error") and call.get("input_tokens") is not None
                 and call.get("output_tokens") is not None)
        if known:
            self.spent_tokens += call["input_tokens"] + call["output_tokens"]
            cost = call_cost(call, self.prices)
            if cost is None and self.max_cost_usd is not None:
                self.stop_reason = self.stop_reason or f"cost unknown for a call to {model!r}"
            self.spent_cost_usd += cost or 0.0
            if model in self.reserve and call["input_tokens"] > self.reserve[model][0]:
                self.stop_reason = self.stop_reason or (
                    f"call to {model!r} used {call['input_tokens']} input tokens, above the "
                    f"reserved ceiling of {self.reserve[model][0]}")
        unknown_attempts = attempts - 1 if known else attempts
        if unknown_attempts:
            if model not in self.reserve:
                self.stop_reason = self.stop_reason or f"usage unknown for a call to {model!r}"
                return
            t, c = self._attempt_worst_case(model)
            self.spent_tokens += unknown_attempts * t
            self.spent_cost_usd += unknown_attempts * (c or 0.0)

    def exceeded(self) -> Optional[str]:
        if self.stop_reason:
            return self.stop_reason
        need_tokens, need_cost = self.next_call_reserve()
        if self.max_total_tokens is not None and (
                self.spent_tokens >= self.max_total_tokens
                or self.spent_tokens + need_tokens > self.max_total_tokens):
            return (f"token budget reached: {self.spent_tokens} spent + {need_tokens} reserved "
                    f"for the next call > {self.max_total_tokens}")
        if self.max_cost_usd is not None and (
                self.spent_cost_usd >= self.max_cost_usd
                or self.spent_cost_usd + need_cost > self.max_cost_usd):
            return (f"cost budget reached: ${self.spent_cost_usd:.4f} spent + ${need_cost:.4f} "
                    f"reserved for the next call > ${self.max_cost_usd:.4f}")
        return None
