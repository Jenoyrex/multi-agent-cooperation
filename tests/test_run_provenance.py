"""Run-level provenance, infrastructure-failure handling, usage/cost
accounting, budgets, and config validation. Mock agents only; no API."""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.agents.base import CallRecord
from src.agents.generation import GenerationConfig
from src.agents.mock_agent import MockAgent
from src.agents.schema import TransportError
from src.experiments.config import ExperimentConfig, estimate_cost, observed_usage
from src.experiments.runner import run_batch

# Converges in 2 rounds regardless of the valuations: A claims 60% of every
# category, so B always gets ~40 points >= its 30% threshold and accepts.
FAST = dict(opening_ask=0.6, min_ask=0.3, concession_step=0.1, accept_threshold=0.3)


def cfg(**kw) -> ExperimentConfig:
    base = dict(mode="smoke", method="baseline_test", model_A_label="a", model_B_label="b",
                num_negotiations=3, base_seed=500, max_rounds=6)
    base.update(kw)
    return ExperimentConfig(**base)


class UsageAgent(MockAgent):
    """Mock that reports API usage like a real agent would."""

    def __init__(self, model="m", **kw):
        super().__init__(**{**FAST, **kw})
        self.model = model

    async def generate_response(self, view):
        action = await super().generate_response(view)
        self.last_call = CallRecord(model=self.model, raw_output="{}", input_tokens=100,
                                    output_tokens=10, latency_s=0.5, attempts=1)
        return action


class FlakyAgent(MockAgent):
    """Raises a transport failure on its `fail_at`-th call (1-based), across
    negotiations, then behaves normally."""

    def __init__(self, fail_at, **kw):
        super().__init__(**{**FAST, **kw})
        self.fail_at, self.n = fail_at, 0

    async def generate_response(self, view):
        self.n += 1
        if self.n == self.fail_at:
            self.last_call = CallRecord(error="ConnectionError: boom", attempts=3, latency_s=1.0)
            raise TransportError("ConnectionError: boom", attempts=3, latency_s=1.0)
        return await super().generate_response(view)


async def run(config, agent_A, agent_B, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "r.db"
        records = await run_batch(config, agent_A, agent_B, db, **kw)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            snap = {
                "runs": [dict(r) for r in conn.execute("SELECT * FROM runs")],
                "negs": [dict(r) for r in conn.execute("SELECT * FROM negotiations ORDER BY negotiation_index")],
                "aborted": [dict(r) for r in conn.execute("SELECT * FROM aborted_negotiations ORDER BY id")],
                "observed": observed_usage(conn),
            }
        finally:
            conn.close()
    return records, snap


# ------------------------------------------------------------ provenance
@pytest.mark.asyncio
async def test_run_row_stores_full_configuration():
    config = cfg(first_mover_policy="B")
    records, snap = await run(config, MockAgent(name="mA", **FAST), MockAgent(name="mB", **FAST))
    (run_row,) = snap["runs"]

    assert len(run_row["run_id"]) == 32 and run_row["status"] == "completed"
    for col, expected in dict(base_seed=500, num_negotiations=3, max_rounds=6, num_categories=3,
                              first_mover_policy="B", method="baseline_test", mode="smoke").items():
        assert run_row[col] == expected
    assert run_row["protocol_id"] and run_row["spec_version"].startswith("sha256:")
    assert len(run_row["prompt_hash"]) == 64
    assert run_row["code_version"]  # commit hash (+dirty) or "unknown"
    assert "python" in json.loads(run_row["dependency_versions_json"])

    full = json.loads(run_row["config_json"])
    assert full["experiment"]["max_rounds"] == 6 and full["experiment"]["base_seed"] == 500
    env = full["environment"]
    assert env["total_points"] == 100 and env["min_qty"] == 10 and env["max_qty"] == 50
    assert env["category_names"] == ["widgets", "gadgets", "components"]
    assert full["agents"]["A"]["name"] == "mA"
    assert full["agents"]["A"]["params"]["opening_ask"] == 0.6  # mock parameters recorded
    assert full["agents"]["A"]["generation_config"] is None

    totals = json.loads(run_row["totals_json"])
    assert totals["completed_negotiations"] == 3 and totals["aborted_attempts"] == 0
    assert all(r.run_id == run_row["run_id"] for r in records)


@pytest.mark.asyncio
async def test_negotiations_reference_run_and_index():
    _, snap = await run(cfg(), MockAgent(**FAST), MockAgent(**FAST))
    run_id = snap["runs"][0]["run_id"]
    assert [(n["run_id"], n["negotiation_index"]) for n in snap["negs"]] == [(run_id, 0), (run_id, 1), (run_id, 2)]
    assert [n["instance_seed"] for n in snap["negs"]] == [500, 501, 502]
    assert all(n["max_rounds"] == 6 for n in snap["negs"])


@pytest.mark.asyncio
async def test_each_run_gets_its_own_id():
    _, s1 = await run(cfg(), MockAgent(**FAST), MockAgent(**FAST))
    _, s2 = await run(cfg(), MockAgent(**FAST), MockAgent(**FAST))
    assert s1["runs"][0]["run_id"] != s2["runs"][0]["run_id"]


# ------------------------------------------------- transport failures
@pytest.mark.asyncio
async def test_transport_failure_reruns_whole_negotiation_when_configured():
    # A's 2nd call (negotiation 0, if A moves first at index 0? A moves at
    # turn 1 only) -> use B, which responds on turn 2 of each negotiation.
    config = cfg(num_negotiations=2, max_transport_reruns=1, first_mover_policy="A")
    records, snap = await run(config, MockAgent(**FAST), FlakyAgent(fail_at=1))

    assert [n["outcome"] for n in snap["negs"]] == ["agreed", "agreed"]
    assert len(snap["negs"]) == 2 and snap["runs"][0]["status"] == "completed"
    (ab,) = snap["aborted"]
    assert (ab["negotiation_index"], ab["attempt_number"], ab["reason"], ab["will_rerun"]) == \
        (0, 1, "transport_failure", 1)
    assert ab["actor"] == "B" and ab["round_number"] == 2
    assert len(json.loads(ab["transcript_json"])) == 1  # partial transcript kept, not resumed
    assert json.loads(snap["runs"][0]["totals_json"])["aborted_attempts"] == 1
    # The rerun is a complete negotiation from turn 1, not a continuation.
    assert snap["negs"][0]["num_rounds"] == 2 and len(json.loads(snap["negs"][0]["transcript_json"])) == 2


@pytest.mark.asyncio
async def test_transport_failure_without_rerun_is_not_counted_as_any_outcome():
    config = cfg(num_negotiations=3, max_transport_reruns=0, first_mover_policy="A")
    records, snap = await run(config, MockAgent(**FAST), FlakyAgent(fail_at=1))

    assert snap["runs"][0]["status"] == "incomplete"
    assert [n["negotiation_index"] for n in snap["negs"]] == [1, 2]  # index 0 has no outcome row
    assert len(records) == 2
    outcomes = json.loads(snap["runs"][0]["totals_json"])["outcomes"]
    assert sum(outcomes.values()) == 2
    assert not {"timeout", "walked_away", "invalid_action"} & set(outcomes)
    (ab,) = snap["aborted"]
    assert ab["will_rerun"] == 0 and ab["reason"] == "transport_failure"


# --------------------------------------------------- usage and budgets
@pytest.mark.asyncio
async def test_usage_and_cost_aggregated_per_negotiation_and_run():
    prices = {"m": (1.0, 2.0)}  # USD per million tokens (input, output)
    records, snap = await run(cfg(), UsageAgent(), UsageAgent(), prices=prices)

    n0 = snap["negs"][0]
    assert n0["api_calls"] == 2 and n0["api_attempts"] == 2  # 2 turns to agree
    assert n0["input_tokens"] == 200 and n0["output_tokens"] == 20
    assert n0["latency_s"] == 1.0
    assert n0["cost_usd"] == pytest.approx(2 * (100 * 1.0 + 10 * 2.0) / 1e6)
    calls = json.loads(n0["calls_json"])
    assert [c["turn_number"] for c in calls] == [1, 2] and calls[0]["raw_output"] == "{}"

    totals = json.loads(snap["runs"][0]["totals_json"])
    assert totals["api_calls"] == 6 and totals["input_tokens"] == 600 and totals["output_tokens"] == 60
    assert totals["cost_usd"] == pytest.approx(6 * 120 / 1e6)
    assert snap["observed"] == {"avg_input_tokens_per_call": 100.0, "avg_output_tokens_per_call": 10.0}


@pytest.mark.asyncio
async def test_mock_runs_have_null_usage_not_zero():
    _, snap = await run(cfg(), MockAgent(**FAST), MockAgent(**FAST))
    n = snap["negs"][0]
    assert n["api_calls"] == 0 and n["input_tokens"] is None and n["cost_usd"] is None


@pytest.mark.asyncio
async def test_token_budget_is_a_hard_stop_and_recorded_separately():
    # Each call = 110 tokens; a negotiation = 2 calls (220). Cap 250: the
    # 1st negotiation completes, the 2nd is stopped before its 2nd call.
    config = cfg(mode="pilot", num_negotiations=3, budget_max_total_tokens=250)
    records, snap = await run(config, UsageAgent(), UsageAgent())

    assert snap["runs"][0]["status"] == "budget_exhausted"
    assert len(snap["negs"]) == 1
    (ab,) = snap["aborted"]
    assert ab["reason"] == "budget_exhausted" and ab["negotiation_index"] == 1 and ab["will_rerun"] == 0
    total = json.loads(snap["runs"][0]["totals_json"])
    assert total["input_tokens"] + total["output_tokens"] == 330  # cap + at most one call


@pytest.mark.asyncio
async def test_cost_budget_requires_prices_and_stops():
    gen = GenerationConfig(model="m", temperature=0, max_output_tokens=10, timeout_s=1, max_retries=0)
    a = UsageAgent()
    a.config = gen
    with pytest.raises(ValueError, match="prices"):
        await run(cfg(mode="pilot", budget_max_cost_usd=0.001), a, UsageAgent())

    prices = {"m": (1000.0, 1000.0)}  # 110 tokens = $0.11 per call
    config = cfg(mode="pilot", num_negotiations=3, budget_max_cost_usd=0.15)
    _, snap = await run(config, a, UsageAgent(), prices=prices)
    assert snap["runs"][0]["status"] == "budget_exhausted"
    assert snap["aborted"][0]["reason"] == "budget_exhausted"


# ---------------------------------------------- validation and estimates
def test_max_rounds_below_two_rejected():
    for bad in (1, 0):
        with pytest.raises(ValueError, match="max_rounds"):
            cfg(max_rounds=bad)
    cfg(max_rounds=2)


def test_pilot_and_full_require_a_budget():
    for mode in ("pilot", "full"):
        with pytest.raises(ValueError, match="budget"):
            cfg(mode=mode, num_negotiations=2)
    cfg(mode="pilot", num_negotiations=2, budget_max_total_tokens=1000)


def test_estimate_uses_max_output_tokens_prices_and_observed_usage():
    gen = GenerationConfig(model="m", temperature=0, max_output_tokens=200, timeout_s=1, max_retries=0)
    config = cfg(num_negotiations=2, max_rounds=4)  # 2 negs * 2 turns/agent = 4 calls per agent
    prices = {"m": (1.0, 2.0)}

    est = estimate_cost(config, {"A": gen, "B": gen}, prices)
    assert est["estimated_api_calls_upper_bound"] == 8 and est["input_basis"] == "assumed"
    assert est["estimated_output_tokens_upper_bound"] == 8 * 200
    assert est["estimated_cost_usd_upper_bound"] is not None

    obs = {"avg_input_tokens_per_call": 100.0, "avg_output_tokens_per_call": 10.0}
    est = estimate_cost(config, {"A": gen, "B": gen}, prices, observed=obs)
    assert est["input_basis"] == "observed" and est["estimated_input_tokens_upper_bound"] == 800
    assert est["estimated_cost_usd_upper_bound"] == pytest.approx((800 * 1.0 + 1600 * 2.0) / 1e6)

    est = estimate_cost(config, {"A": gen, "B": gen})  # no prices -> no dollars, tokens still known
    assert est["estimated_cost_usd_upper_bound"] is None and est["estimated_tokens_upper_bound"]

    est = estimate_cost(config)  # nothing known about outputs
    assert est["estimated_output_tokens_upper_bound"] is None
