"""SQLite storage for runs, negotiations and aborted negotiations.
See docs/spec.md §5.7, §Reproducibility.

Three tables:
  runs                  one row per run: full config + code/spec/prompt/
                        dependency provenance + final status and totals.
  negotiations          one row per COMPLETED negotiation (one of the four
                        strategic outcomes), tied to a run by
                        (run_id, negotiation_index), which is unique.
  aborted_negotiations  infrastructure failures (transport failure, budget
                        exhausted). Never counted as an outcome; the partial
                        transcript and call log are kept for diagnostics.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from src.negotiation.protocol import NegotiationAborted, NegotiationRecord
from src.negotiation.usage import Prices, summarize_calls

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,              -- running|completed|incomplete|budget_exhausted|failed
    method TEXT NOT NULL,
    mode TEXT NOT NULL,
    base_seed INTEGER NOT NULL,
    num_negotiations INTEGER NOT NULL,
    max_rounds INTEGER NOT NULL,
    num_categories INTEGER NOT NULL,
    first_mover_policy TEXT NOT NULL,
    protocol_id TEXT NOT NULL,
    spec_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    code_version TEXT NOT NULL,
    dependency_versions_json TEXT NOT NULL,
    config_json TEXT NOT NULL,         -- complete experiment configuration
    totals_json TEXT                   -- usage/cost/outcome counts, set on finish
);

CREATE TABLE IF NOT EXISTS negotiations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    negotiation_index INTEGER NOT NULL,
    instance_seed INTEGER NOT NULL,
    method TEXT NOT NULL,
    model_A TEXT NOT NULL,
    model_B TEXT NOT NULL,
    first_mover TEXT NOT NULL,
    first_mover_policy TEXT NOT NULL DEFAULT 'explicit',
    max_rounds INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    invalid_reason TEXT,
    invalid_detail TEXT,
    num_rounds INTEGER NOT NULL,
    utility_A REAL NOT NULL,
    utility_B REAL NOT NULL,
    total_welfare REAL NOT NULL,
    optimal_welfare REAL NOT NULL,
    final_allocation_json TEXT,
    resource_pool_json TEXT NOT NULL,
    valuation_A_json TEXT NOT NULL,
    valuation_B_json TEXT NOT NULL,
    transcript_json TEXT NOT NULL,
    calls_json TEXT NOT NULL,          -- per-turn raw model output, tokens, latency, attempts
    api_calls INTEGER NOT NULL DEFAULT 0,
    api_attempts INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,              -- NULL = provider reported nothing
    output_tokens INTEGER,
    latency_s REAL,
    cost_usd REAL,
    timestamp REAL NOT NULL,
    UNIQUE (run_id, negotiation_index)
);

CREATE TABLE IF NOT EXISTS aborted_negotiations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    negotiation_index INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,   -- 1-based; each attempt is a full re-run
    reason TEXT NOT NULL,              -- transport_failure|budget_exhausted
    detail TEXT NOT NULL,
    actor TEXT NOT NULL,
    round_number INTEGER NOT NULL,
    will_rerun INTEGER NOT NULL,
    transcript_json TEXT NOT NULL,
    calls_json TEXT NOT NULL,
    api_calls INTEGER NOT NULL DEFAULT 0,
    api_attempts INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_s REAL,
    cost_usd REAL,
    timestamp REAL NOT NULL,
    UNIQUE (run_id, negotiation_index, attempt_number)
);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    has_tables = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='negotiations'"
    ).fetchone()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if has_tables and version != SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"{db_path} has schema version {version}, expected {SCHEMA_VERSION}. "
            "Delete it or migrate it; refusing to write mixed-schema data."
        )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def _turn_to_dict(turn) -> dict:
    return {
        "turn_number": turn.turn_number,
        "actor": turn.actor,
        "action": {
            "action_type": turn.action.action_type,
            "allocation": turn.action.allocation,
            "message": turn.action.message,
        },
    }


def save_run(conn: sqlite3.Connection, run_id: str, meta: dict) -> None:
    """`meta` comes from runner.build_run_metadata."""
    conn.execute(
        """
        INSERT INTO runs (run_id, started_at, status, method, mode, base_seed,
            num_negotiations, max_rounds, num_categories, first_mover_policy,
            protocol_id, spec_version, prompt_hash, code_version,
            dependency_versions_json, config_json)
        VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, time.time(), meta["method"], meta["mode"], meta["base_seed"],
            meta["num_negotiations"], meta["max_rounds"], meta["num_categories"],
            meta["first_mover_policy"], meta["protocol_id"], meta["spec_version"],
            meta["prompt_hash"], meta["code_version"],
            json.dumps(meta["dependency_versions"]), json.dumps(meta["config"]),
        ),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, totals: dict) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, totals_json = ? WHERE run_id = ?",
        (time.time(), status, json.dumps(totals), run_id),
    )
    conn.commit()


def save_record(conn: sqlite3.Connection, record: NegotiationRecord) -> int:
    cur = conn.execute(
        """
        INSERT INTO negotiations (
            run_id, negotiation_index, instance_seed, method, model_A, model_B,
            first_mover, first_mover_policy, max_rounds, outcome, invalid_reason,
            invalid_detail, num_rounds, utility_A, utility_B, total_welfare,
            optimal_welfare, final_allocation_json, resource_pool_json,
            valuation_A_json, valuation_B_json, transcript_json, calls_json,
            api_calls, api_attempts, input_tokens, output_tokens, latency_s,
            cost_usd, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.run_id,
            record.negotiation_index,
            record.instance_seed,
            record.method,
            record.model_A,
            record.model_B,
            record.first_mover,
            record.first_mover_policy,
            record.max_rounds,
            record.outcome,
            record.invalid_reason,
            record.invalid_detail,
            record.num_rounds,
            record.utility_A,
            record.utility_B,
            record.total_welfare,
            record.optimal_welfare,
            json.dumps(record.final_allocation),
            json.dumps(record.resource_pool),
            json.dumps(record.valuation_A),
            json.dumps(record.valuation_B),
            json.dumps([_turn_to_dict(t) for t in record.transcript]),
            json.dumps(record.calls),
            record.api_calls,
            record.api_attempts,
            record.input_tokens,
            record.output_tokens,
            record.latency_s,
            record.cost_usd,
            record.timestamp,
        ),
    )
    conn.commit()
    return cur.lastrowid


def save_aborted(
    conn: sqlite3.Connection,
    run_id: str,
    negotiation_index: int,
    attempt_number: int,
    aborted: NegotiationAborted,
    will_rerun: bool,
    prices: Optional[Prices] = None,
) -> int:
    u = summarize_calls(aborted.calls, prices)
    cur = conn.execute(
        """
        INSERT INTO aborted_negotiations (
            run_id, negotiation_index, attempt_number, reason, detail, actor,
            round_number, will_rerun, transcript_json, calls_json, api_calls,
            api_attempts, input_tokens, output_tokens, latency_s, cost_usd, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, negotiation_index, attempt_number, aborted.reason, aborted.detail,
            aborted.actor, aborted.round_number, int(will_rerun),
            json.dumps([_turn_to_dict(t) for t in aborted.transcript]),
            json.dumps(aborted.calls),
            u["api_calls"], u["api_attempts"], u["input_tokens"], u["output_tokens"],
            u["latency_s"], u["cost_usd"], time.time(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def run_totals(conn: sqlite3.Connection, run_id: str) -> dict:
    """Outcome counts plus usage aggregated over completed AND aborted
    negotiations of a run (aborted attempts still consumed tokens)."""
    def usage(table: str) -> tuple:
        return conn.execute(
            f"SELECT COALESCE(SUM(api_calls),0), COALESCE(SUM(api_attempts),0), "
            f"SUM(input_tokens), SUM(output_tokens), SUM(latency_s), SUM(cost_usd) "
            f"FROM {table} WHERE run_id = ?", (run_id,)
        ).fetchone()

    def add(a, b):
        return b if a is None else a if b is None else a + b

    done, aborted = usage("negotiations"), usage("aborted_negotiations")
    keys = ["api_calls", "api_attempts", "input_tokens", "output_tokens", "latency_s", "cost_usd"]
    totals = {k: add(d, a) for k, d, a in zip(keys, done, aborted)}
    totals["outcomes"] = dict(conn.execute(
        "SELECT outcome, COUNT(*) FROM negotiations WHERE run_id = ? GROUP BY outcome", (run_id,)
    ).fetchall())
    totals["completed_negotiations"] = sum(totals["outcomes"].values())
    totals["aborted_attempts"] = conn.execute(
        "SELECT COUNT(*) FROM aborted_negotiations WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    return totals


def load_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM negotiations").fetchall()
