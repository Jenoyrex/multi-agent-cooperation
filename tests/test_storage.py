import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.negotiation.protocol import NegotiationRecord
from src.storage.db import get_connection, load_all, save_record, save_run

RUN_META = dict(
    method="baseline", mode="smoke", base_seed=1, num_negotiations=1, max_rounds=10,
    num_categories=1, first_mover_policy="A", protocol_id="p", spec_version="s",
    prompt_hash="h", code_version="c", dependency_versions={}, config={},
)


def _record(**kw) -> NegotiationRecord:
    defaults = dict(
        run_id="run1",
        negotiation_index=0,
        max_rounds=10,
        instance_seed=42,
        method="baseline",
        model_A="mock-A",
        model_B="mock-B",
        first_mover="A",
        outcome="agreed",
        num_rounds=3,
        final_allocation={"A": {"widgets": 5}, "B": {"widgets": 5}},
        utility_A=50.0,
        utility_B=50.0,
        total_welfare=100.0,
        optimal_welfare=100.0,
        resource_pool={"widgets": 10},
        valuation_A={"widgets": 5.0},
        valuation_B={"widgets": 5.0},
    )
    defaults.update(kw)
    return NegotiationRecord(**defaults)


def test_save_and_load_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(Path(tmp) / "test.db")
        try:
            save_run(conn, "run1", RUN_META)
            row_id = save_record(conn, _record())
            rows = load_all(conn)
            assert row_id == 1
            assert len(rows) == 1
            assert rows[0]["instance_seed"] == 42
            assert rows[0]["outcome"] == "agreed"
            assert rows[0]["utility_A"] == 50.0
            assert rows[0]["run_id"] == "run1" and rows[0]["negotiation_index"] == 0
            assert rows[0]["max_rounds"] == 10
        finally:
            conn.close()


def test_run_id_and_index_are_unique_and_required():
    with tempfile.TemporaryDirectory() as tmp:
        conn = get_connection(Path(tmp) / "test.db")
        try:
            save_run(conn, "run1", RUN_META)
            save_record(conn, _record())
            with pytest.raises(sqlite3.IntegrityError):  # duplicate (run_id, index)
                save_record(conn, _record())
            save_record(conn, _record(negotiation_index=1))  # different index is fine
            with pytest.raises(sqlite3.IntegrityError):  # unknown run (foreign key)
                save_record(conn, _record(run_id="nope"))
            with pytest.raises(sqlite3.IntegrityError):  # no run at all
                save_record(conn, _record(run_id=None))
        finally:
            conn.close()


def test_old_schema_version_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.db"
        old = sqlite3.connect(str(path))
        old.execute("CREATE TABLE negotiations (id INTEGER PRIMARY KEY)")
        old.commit()
        old.close()
        with pytest.raises(RuntimeError, match="schema version"):
            get_connection(path)
