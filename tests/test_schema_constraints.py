"""The database itself refuses rows that break the frozen outcome semantics
(spec §8.5, §8.6), so a bug upstream cannot store an impossible record."""
import sqlite3

import pytest

from src.storage.db import get_connection

RUN = dict(run_id="r", started_at=0.0, status="running", method="baseline_v1", mode="pilot",
           base_seed=30000, num_negotiations=3, max_rounds=10, num_categories=3,
           first_mover_policy="A", protocol_id="p", spec_version="s", prompt_hash="h",
           code_version="c", dependency_versions_json="{}", config_json="{}")
ALLOC = '{"A": {"widgets": 1}, "B": {"widgets": 2}}'
NEG = dict(run_id="r", negotiation_index=0, instance_seed=30000, method="baseline_v1",
           model_A="claude:x", model_B="openai:y", first_mover="A", max_rounds=10,
           outcome="agreed", invalid_reason=None, num_rounds=2, utility_A=40.0, utility_B=50.0,
           total_welfare=90.0, optimal_welfare=120.0, final_allocation_json=ALLOC,
           resource_pool_json="{}", valuation_A_json="{}", valuation_B_json="{}",
           transcript_json="[]", calls_json="[]", timestamp=0.0)
FAILED = dict(utility_A=0.0, utility_B=0.0, total_welfare=0.0, final_allocation_json="null")


def insert(conn, table, row):
    conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                 tuple(row.values()))


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "x.db")
    insert(c, "runs", RUN)
    yield c
    c.close()


@pytest.mark.parametrize("row", [
    {},
    {"outcome": "walked_away", **FAILED},
    {"outcome": "timeout", **FAILED},
    *({"outcome": "invalid_action", "invalid_reason": r, **FAILED}
      for r in ("malformed_output", "invalid_allocation", "illegal_accept")),
])
def test_valid_rows_are_accepted(conn, row):
    insert(conn, "negotiations", {**NEG, **row})


@pytest.mark.parametrize("row", [
    {"outcome": "illegal_action", **FAILED},                                  # not a frozen outcome
    {"outcome": "invalid_action", "invalid_reason": "illegal_action", **FAILED},  # not a frozen reason
    {"outcome": "invalid_action", **FAILED},                                  # invalid without a reason
    {"outcome": "timeout", "invalid_reason": "malformed_output", **FAILED},   # reason without invalid
    {"final_allocation_json": "null"},                                        # agreed without allocation
    {"outcome": "timeout", **{**FAILED, "final_allocation_json": ALLOC}},     # failure with allocation
    {"outcome": "walked_away", **{**FAILED, "utility_A": 10.0}},              # failure not scored 0
    {"first_mover": "C"},
])
def test_rows_breaking_frozen_semantics_are_refused(conn, row):
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "negotiations", {**NEG, **row})


def test_unknown_abort_reason_and_run_status_are_refused(conn):
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "runs", {**RUN, "run_id": "r2", "status": "done"})
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "aborted_negotiations", dict(
            run_id="r", negotiation_index=0, attempt_number=1, reason="crash", detail="",
            actor="A", round_number=1, will_rerun=0, transcript_json="[]", calls_json="[]",
            timestamp=0.0))
