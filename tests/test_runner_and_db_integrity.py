"""Verification-pass tests: the runner executes each negotiation exactly
once (unless explicitly re-run after a transport failure), the SQLite schema
works end to end on a fresh file, unknown usage stays unknown, and run
provenance is complete when real agents (stub clients) are used."""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.agents.claude_agent import ClaudeAgent
from src.agents.generation import GenerationConfig
from src.agents.mock_agent import MockAgent
from src.agents.openai_agent import OpenAIAgent
from src.negotiation.protocol import NegotiationAborted, NegotiationSession
from src.negotiation.usage import summarize_calls
from src.experiments.runner import _build_instance_state, build_run_metadata
from src.storage.db import (SCHEMA_VERSION, finish_run, get_connection, load_all, run_totals,
                            save_aborted, save_record, save_run)
from tests.test_agents_stub import StubClient, claude_resp, openai_resp
from tests.test_run_provenance import FAST, FlakyAgent, UsageAgent, cfg, run


class CountingAgent(MockAgent):
    def __init__(self, **kw):
        super().__init__(**{**FAST, **kw})
        self.calls = 0

    async def generate_response(self, view):
        self.calls += 1
        return await super().generate_response(view)


# ------------------------------------------------ exactly-once execution
@pytest.mark.asyncio
async def test_each_negotiation_runs_and_is_stored_exactly_once():
    a, b = CountingAgent(), CountingAgent()
    records, snap = await run(cfg(num_negotiations=3, first_mover_policy="A"), a, b)

    # 3 negotiations x (A offers, B accepts) = exactly 3 calls per agent.
    assert (a.calls, b.calls) == (3, 3)
    assert len(records) == 3 and len(snap["negs"]) == 3 and snap["aborted"] == []
    assert [n["negotiation_index"] for n in snap["negs"]] == [0, 1, 2]
    assert [n["instance_seed"] for n in snap["negs"]] == [500, 501, 502]


@pytest.mark.asyncio
async def test_transport_failure_reruns_that_negotiation_exactly_once():
    a, b = CountingAgent(), FlakyAgent(fail_at=1)
    _, snap = await run(cfg(num_negotiations=2, max_transport_reruns=1, first_mover_policy="A"), a, b)

    # neg 0: attempt 1 (A ok, B fails) + rerun (A ok, B ok); neg 1: A ok, B ok.
    assert (a.calls, b.calls) == (3, 3)
    assert [n["negotiation_index"] for n in snap["negs"]] == [0, 1]  # one completed row each
    assert [(x["negotiation_index"], x["attempt_number"]) for x in snap["aborted"]] == [(0, 1)]


@pytest.mark.asyncio
async def test_no_rerun_when_not_configured():
    a, b = CountingAgent(), FlakyAgent(fail_at=1)
    _, snap = await run(cfg(num_negotiations=2, max_transport_reruns=0, first_mover_policy="A"), a, b)

    assert (a.calls, b.calls) == (2, 2)  # neg 0 once (aborted), neg 1 once
    assert [n["negotiation_index"] for n in snap["negs"]] == [1]
    assert len(snap["aborted"]) == 1


@pytest.mark.asyncio
async def test_budget_is_checked_before_each_call():
    a, b = UsageAgent(), UsageAgent()  # 110 tokens per call
    _, snap = await run(cfg(mode="smoke", num_negotiations=3, budget_max_total_tokens=100,
                            first_mover_policy="A"), a, b)

    # After A's first call (110 >= 100) B's call must never start.
    assert (a.calls, b.calls) == (1, 0)
    assert snap["negs"] == [] and [x["reason"] for x in snap["aborted"]] == ["budget_exhausted"]
    assert snap["runs"][0]["status"] == "budget_exhausted"


# ------------------------------------------------- fresh end-to-end SQLite
@pytest.mark.asyncio
async def test_fresh_sqlite_end_to_end_with_reopen():
    config = cfg(num_negotiations=1, first_mover_policy="A")
    a, b = UsageAgent(name="ua"), UsageAgent(name="ub")
    prices = {"m": (1.0, 2.0)}
    meta = build_run_metadata(config, a, b, prices)
    state = _build_instance_state(500, config, a, b, index=0, run_id="run-e2e")
    record = await NegotiationSession(a, b, state, prices=prices).run()
    aborted = NegotiationAborted(
        "transport_failure", "ConnectionError: boom", 2, "B", record.transcript[:1],
        [{"turn_number": 2, "actor": "B", "error": "ConnectionError: boom", "attempts": 3,
          "latency_s": 1.5, "model": "m"}])

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fresh.db"
        assert not path.exists()

        conn = get_connection(path)                                   # 1 create DB
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            for table in ("runs", "negotiations", "aborted_negotiations"):
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                assert len(cols) == len(set(cols)), table              # every column exactly once
            save_run(conn, "run-e2e", meta)                            # 2 create run
            row_id = save_record(conn, record)                         # 3 save completed negotiation
            (row,) = load_all(conn)                                    # 4 load it
            assert row_id == row["id"] == 1
            aborted_id = save_aborted(conn, "run-e2e", 1, 1, aborted, will_rerun=False, prices=prices)  # 5
            totals = run_totals(conn, "run-e2e")                       # 6 totals
            finish_run(conn, "run-e2e", "incomplete", totals)
        finally:
            conn.close()                                               # 7 close

        conn = get_connection(path)                                    # 8 reopen (schema check passes)
        conn.row_factory = sqlite3.Row
        try:                                                           # 9 verify
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

            run_row = dict(conn.execute("SELECT * FROM runs").fetchone())
            assert run_row["status"] == "incomplete" and run_row["finished_at"] is not None
            for col in ("method", "mode", "base_seed", "num_negotiations", "max_rounds",
                        "num_categories", "first_mover_policy", "protocol_id", "spec_version",
                        "prompt_hash", "code_version"):
                assert run_row[col] == meta[col], col
            assert json.loads(run_row["config_json"]) == json.loads(json.dumps(meta["config"]))
            assert json.loads(run_row["dependency_versions_json"]) == meta["dependency_versions"]

            n = dict(conn.execute("SELECT * FROM negotiations").fetchone())
            expected = dict(
                run_id="run-e2e", negotiation_index=0, instance_seed=500, method="baseline_test",
                model_A="ua", model_B="ub", first_mover="A", first_mover_policy="A", max_rounds=6,
                outcome=record.outcome, invalid_reason=None, invalid_detail=None,
                num_rounds=record.num_rounds, utility_A=record.utility_A, utility_B=record.utility_B,
                total_welfare=record.total_welfare, optimal_welfare=record.optimal_welfare,
                api_calls=2, api_attempts=2, input_tokens=200, output_tokens=20, latency_s=1.0,
                timestamp=record.timestamp)
            for col, value in expected.items():
                assert n[col] == value, col
            assert n["cost_usd"] == pytest.approx(240 / 1e6)
            assert json.loads(n["resource_pool_json"]) == record.resource_pool
            assert json.loads(n["valuation_A_json"]) == record.valuation_A
            assert json.loads(n["valuation_B_json"]) == record.valuation_B
            assert json.loads(n["final_allocation_json"]) == record.final_allocation
            assert json.loads(n["calls_json"]) == record.calls
            assert [t["actor"] for t in json.loads(n["transcript_json"])] == ["A", "B"]

            ab = dict(conn.execute("SELECT * FROM aborted_negotiations").fetchone())
            assert ab["id"] == aborted_id
            assert (ab["run_id"], ab["negotiation_index"], ab["attempt_number"], ab["reason"],
                    ab["actor"], ab["round_number"], ab["will_rerun"]) == \
                ("run-e2e", 1, 1, "transport_failure", "B", 2, 0)
            assert (ab["api_calls"], ab["api_attempts"], ab["latency_s"]) == (1, 3, 1.5)
            assert ab["input_tokens"] is None  # failed call: usage unobservable, stays unknown
            assert len(json.loads(ab["transcript_json"])) == 1

            saved_totals = json.loads(run_row["totals_json"])
            assert saved_totals == run_totals(conn, "run-e2e")
            assert saved_totals["api_calls"] == 3 and saved_totals["api_attempts"] == 5
            # The aborted attempt made an API call (api_calls=1) whose usage is
            # unobservable (failed call) -> the run-level total must be None,
            # not the completed negotiation's known 200/20 silently standing in.
            assert saved_totals["input_tokens"] is None and saved_totals["output_tokens"] is None
            assert saved_totals["cost_usd"] is None
            assert saved_totals["outcomes"] == {record.outcome: 1}
            assert saved_totals["completed_negotiations"] == 1 and saved_totals["aborted_attempts"] == 1

            # Constraints hold after reopening.
            with pytest.raises(sqlite3.IntegrityError):
                save_record(conn, record)  # duplicate (run_id, negotiation_index)
            with pytest.raises(sqlite3.IntegrityError):
                save_aborted(conn, "run-e2e", 1, 1, aborted, False)  # duplicate attempt
            with pytest.raises(sqlite3.IntegrityError):
                save_aborted(conn, "ghost-run", 0, 1, aborted, False)  # foreign key
        finally:
            conn.close()


# ------------------------------------------------ unknown usage stays unknown
def test_unknown_usage_is_never_zero_or_a_partial_sum():
    known = {"model": "m", "input_tokens": 10, "output_tokens": 5, "latency_s": 1.0, "attempts": 1}
    unknown = {"model": "m", "input_tokens": None, "output_tokens": None, "latency_s": 1.0, "attempts": 1}
    failed = {"model": "m", "error": "boom", "attempts": 3, "latency_s": 2.0}
    prices = {"m": (1.0, 1.0)}

    assert summarize_calls([], prices)["input_tokens"] is None            # no calls
    assert summarize_calls([known], prices)["input_tokens"] == 10
    partial = summarize_calls([known, unknown], prices)
    assert partial["input_tokens"] is None and partial["cost_usd"] is None  # not 10
    with_failed = summarize_calls([known, failed], prices)                # failed call excluded from tokens
    assert with_failed["input_tokens"] == 10 and with_failed["api_attempts"] == 4
    assert with_failed["latency_s"] == 3.0
    assert summarize_calls([known], None)["cost_usd"] is None             # no price -> unknown cost


def test_run_totals_known_usage_sums_across_rows():
    from tests.test_storage import RUN_META, _record

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(Path(tmp) / "t.db")
        try:
            save_run(conn, "run1", RUN_META)
            save_record(conn, _record(negotiation_index=0, api_calls=1, api_attempts=1,
                                      input_tokens=10, output_tokens=5, cost_usd=0.1))
            save_record(conn, _record(negotiation_index=1, api_calls=2, api_attempts=2,
                                      input_tokens=20, output_tokens=8, cost_usd=0.2))
            totals = run_totals(conn, "run1")
            assert totals["input_tokens"] == 30 and totals["output_tokens"] == 13
            assert totals["cost_usd"] == pytest.approx(0.3) and totals["api_calls"] == 3
        finally:
            conn.close()


def test_run_totals_do_not_hide_unknown_negotiations():
    from tests.test_storage import RUN_META, _record

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(Path(tmp) / "t.db")
        try:
            save_run(conn, "run1", RUN_META)
            save_record(conn, _record(negotiation_index=0, api_calls=1, api_attempts=1,
                                      input_tokens=10, output_tokens=5, cost_usd=0.1))
            assert run_totals(conn, "run1")["input_tokens"] == 10
            save_record(conn, _record(negotiation_index=1, api_calls=1, api_attempts=1))  # usage unknown
            totals = run_totals(conn, "run1")
            assert totals["input_tokens"] is None and totals["output_tokens"] is None
            assert totals["cost_usd"] is None and totals["api_calls"] == 2
        finally:
            conn.close()


def test_run_totals_unknown_aborted_usage_makes_total_none_not_zero():
    """The pre-freeze blocker: an aborted attempt that made a real API call
    with unobservable usage must not be silently dropped from the sum (SQL
    SUM ignores NULLs) while a known completed negotiation makes the total
    look fully known."""
    from tests.test_storage import RUN_META, _record

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(Path(tmp) / "t.db")
        try:
            save_run(conn, "run1", RUN_META)
            save_record(conn, _record(negotiation_index=0, api_calls=1, api_attempts=1,
                                      input_tokens=10, output_tokens=5, cost_usd=0.1))
            failed_call = {"model": "m", "error": "boom", "attempts": 2, "latency_s": 1.0}
            aborted = NegotiationAborted("transport_failure", "boom", 1, "A", [], [failed_call])
            save_aborted(conn, "run1", 1, 1, aborted, will_rerun=False)

            totals = run_totals(conn, "run1")
            assert totals["api_calls"] == 2  # the failed call did happen
            assert totals["input_tokens"] is None and totals["output_tokens"] is None
            assert totals["cost_usd"] is None
        finally:
            conn.close()


def test_run_totals_no_api_call_does_not_falsely_mark_unknown():
    """A mock-only negotiation (no real API calls at all) must not poison
    the run total to None -- unknown usage only applies when a call
    actually happened and its usage wasn't observed."""
    from tests.test_storage import RUN_META, _record

    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(Path(tmp) / "t.db")
        try:
            save_run(conn, "run1", RUN_META)
            save_record(conn, _record(negotiation_index=0, api_calls=1, api_attempts=1,
                                      input_tokens=10, output_tokens=5, cost_usd=0.1))
            save_record(conn, _record(negotiation_index=1))  # no API calls (mock agents)
            totals = run_totals(conn, "run1")
            assert totals["input_tokens"] == 10 and totals["output_tokens"] == 5
            assert totals["cost_usd"] == pytest.approx(0.1)
        finally:
            conn.close()


# ------------------------------------- provenance through the real agents
@pytest.mark.asyncio
async def test_run_and_negotiation_provenance_with_real_agents():
    cfg_a = GenerationConfig(model="claude-x", temperature=0.2, max_output_tokens=111, timeout_s=9,
                             max_retries=1, retry_backoff_s=0)
    cfg_b = GenerationConfig(model="gpt-x", temperature=0.9, max_output_tokens=222, timeout_s=8,
                             max_retries=2, retry_backoff_s=0)
    walk = {"action_type": "WALK_AWAY", "message": "bye"}
    claude = ClaudeAgent(cfg_a, client=StubClient(claude_resp(tool_input=walk)))
    gpt = OpenAIAgent(cfg_b, client=StubClient(openai_resp(content=json.dumps(walk))))
    prices = {"claude-x": (3.0, 15.0), "gpt-x": (5.0, 20.0)}

    # "alternate": index 0 -> A (Claude) moves first, index 1 -> B (GPT) moves first.
    records, snap = await run(cfg(num_negotiations=2, base_seed=700), claude, gpt, prices=prices)

    (run_row,) = snap["runs"]
    full = json.loads(run_row["config_json"])
    assert full["agents"]["A"]["generation_config"] == cfg_a.to_dict()
    assert full["agents"]["B"]["generation_config"] == cfg_b.to_dict()
    assert full["agents"]["A"]["name"] == "claude:claude-x" and full["agents"]["B"]["name"] == "openai:gpt-x"
    assert full["prices_usd_per_million_tokens"] == {"claude-x": [3.0, 15.0], "gpt-x": [5.0, 20.0]}

    n0, n1 = snap["negs"]
    for n, idx, seed, mover in ((n0, 0, 700, "A"), (n1, 1, 701, "B")):
        assert (n["run_id"], n["negotiation_index"], n["instance_seed"], n["first_mover"]) == \
            (run_row["run_id"], idx, seed, mover)
        assert (n["model_A"], n["model_B"], n["max_rounds"]) == ("claude:claude-x", "openai:gpt-x", 6)
        assert n["outcome"] == "walked_away" and len(json.loads(n["transcript_json"])) == 1
        (call,) = json.loads(n["calls_json"])
        assert json.loads(call["raw_output"]) == walk
        assert (call["input_tokens"], call["output_tokens"], call["attempts"]) == (100, 20, 1)
    assert n0["cost_usd"] == pytest.approx((100 * 3.0 + 20 * 15.0) / 1e6)
    assert n1["cost_usd"] == pytest.approx((100 * 5.0 + 20 * 20.0) / 1e6)
    assert json.loads(json.loads(n0["calls_json"])[0]["raw_output"]) == walk
    assert json.loads(n0["calls_json"])[0]["model"] == "claude-x"
    assert json.loads(n1["calls_json"])[0]["model"] == "gpt-x"
