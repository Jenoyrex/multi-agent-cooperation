"""Dashboard data layer: read-only access, metric reuse, preregistration lock."""
import hashlib
import sqlite3

import pytest

from dashboard import data as D
from src.agents.base import TranscriptTurn
from src.agents.mock_agent import MockAgent
from src.agents.schema import NegotiationAction
from src.evaluation import metrics as M
from src.experiments.config import ExperimentConfig
from src.experiments.runner import run_batch
from src.negotiation.protocol import NegotiationRecord
from src.storage.db import get_connection, save_record, save_run

POOL = {"x": 10, "y": 10}
VAL_A = {"x": 6.0, "y": 4.0}   # 100 points over the pool
VAL_B = {"x": 2.0, "y": 8.0}
CLAUDE, GPT = "claude:claude-sonnet-4-6", "openai:gpt-4.1-2025-04-14"


def _meta(mode, method):
    return {"method": method, "mode": mode, "base_seed": 0, "num_negotiations": 1, "max_rounds": 10,
            "num_categories": 2, "first_mover_policy": "A", "protocol_id": "p", "spec_version": "s",
            "prompt_hash": "h", "code_version": "c", "dependency_versions": {}, "config": {}}


def _record(run_id, index, seed, method, outcome, claude_seat="A", first_mover="A", alloc_A=None):
    alloc = None
    turns = []
    if outcome == "agreed":
        a = alloc_A or {"x": 10, "y": 0}
        alloc = {"A": a, "B": {c: POOL[c] - a[c] for c in POOL}}
        second = "B" if first_mover == "A" else "A"
        turns = [TranscriptTurn(1, first_mover, NegotiationAction(action_type="OFFER", allocation=alloc, message="hi")),
                 TranscriptTurn(2, second, NegotiationAction(action_type="ACCEPT"))]
    uA = sum(VAL_A[c] * alloc["A"][c] for c in POOL) if alloc else 0.0
    uB = sum(VAL_B[c] * alloc["B"][c] for c in POOL) if alloc else 0.0
    return NegotiationRecord(
        instance_seed=seed, method=method,
        model_A=CLAUDE if claude_seat == "A" else GPT, model_B=GPT if claude_seat == "A" else CLAUDE,
        first_mover=first_mover, outcome=outcome, num_rounds=2 if alloc else 3, final_allocation=alloc,
        utility_A=uA, utility_B=uB, total_welfare=uA + uB, optimal_welfare=140.0, resource_pool=POOL,
        valuation_A=VAL_A, valuation_B=VAL_B, transcript=turns, max_rounds=10,
        invalid_reason="illegal_accept" if outcome == "invalid_action" else None,
        run_id=run_id, negotiation_index=index)


def _db(path, mode, records_by_method):
    conn = get_connection(path)
    for method, recs in records_by_method.items():
        run_id = f"{mode}-{method}"
        save_run(conn, run_id, _meta(mode, method))
        for i, spec in enumerate(recs):
            save_record(conn, _record(run_id, i, **spec, method=method))
    conn.close()


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_empty_results_dir_shows_no_data(tmp_path):
    snap = D.Store(tmp_path).snapshot()
    assert snap["negotiations"] == [] and snap["databases"] == []
    assert snap["progress"]["pilot"] == {"done": 0, "target": 24}
    assert snap["progress"]["full"] == {"done": 0, "target": 480}
    assert snap["results"]["pilot"]["pooled"] is None
    assert snap["results"]["full"]["locked"] is True
    assert list(tmp_path.iterdir()) == []  # nothing created


async def test_mock_run_is_read_back_unchanged_and_with_engine_metrics(tmp_path):
    db = tmp_path / "smoke.db"
    config = ExperimentConfig(mode="smoke", method="baseline_plain_prompting", model_A_label="a",
                              model_B_label="b", num_negotiations=5, base_seed=1000)
    records = await run_batch(config, MockAgent(name="mock-A"), MockAgent(name="mock-B"), db)
    before = _digest(db)

    store = D.Store(tmp_path)
    snap = store.snapshot()
    assert _digest(db) == before  # read-only
    rows = snap["negotiations"]
    assert [r["mode"] for r in rows] == ["smoke"] * 5
    assert snap["progress"]["other"] == {"smoke": 5}
    for row, rec in zip(rows, records):
        assert row["outcome"] == rec.outcome and row["rounds"] == rec.num_rounds
        assert row["rwe"] == M.relative_welfare_efficiency(rec)
        assert row["equitability"] == M.equitability(rec)
        assert row["imbalance"] is None  # not a Claude/GPT pair

    detail = store.negotiation(rows[0]["key"])
    assert len(detail["events"]) == len(records[0].transcript) + (records[0].outcome == "invalid_action")
    assert detail["valuation_A"] == records[0].valuation_A
    assert _digest(db) == before
    assert store.negotiation("smoke:999999") is None
    assert store.negotiation("nope:1") is None


def test_pilot_view_by_condition_and_paired_differences(tmp_path):
    _db(tmp_path / "pilot.db", "pilot", {
        "baseline_v1": [dict(seed=30000, outcome="agreed"), dict(seed=30000, outcome="walked_away")],
        "structured_v1": [dict(seed=30000, outcome="agreed", claude_seat="B", first_mover="B"),
                          dict(seed=30000, outcome="agreed")],
    })
    view = D.Store(tmp_path).snapshot()["results"]["pilot"]
    assert view["locked"] is False and view["n"] == 4
    base, struct = view["by_condition"]["baseline_v1"], view["by_condition"]["structured_v1"]
    # agreed: uA=60, uB=80, W*=140 -> RWE 1.0, EW 0.6; the walk-away scores 0.
    assert base["agreement_rate"] == 0.5 and base["rwe"] == 0.5 and base["ew"] == 0.3
    assert base["n_agreed"] == 1 and base["equitability"] == pytest.approx(0.8)
    assert struct["agreement_rate"] == 1.0 and struct["rwe"] == 1.0
    # imbalance is by MODEL: Claude in seat A -> 0.6-0.8, in seat B -> 0.8-0.6.
    assert struct["imbalance"] == pytest.approx(0.0) and base["imbalance"] == pytest.approx(-0.2)
    assert struct["first_mover_gap"] == pytest.approx(0.0)  # A-first -0.2, B-first +0.2
    [d] = view["paired"]
    assert d["seed"] == 30000 and d["n_baseline"] == 2 and d["n_structured"] == 2
    assert d["rwe"] == pytest.approx(0.5) and d["ew"] == pytest.approx(0.3)
    assert d["agreement_rate"] == pytest.approx(0.5)


def test_incomplete_full_run_is_locked(tmp_path):
    _db(tmp_path / "full.db", "full", {"baseline_v1": [dict(seed=40000, outcome="agreed")],
                                       "structured_v1": [dict(seed=40000, outcome="timeout")]})
    snap = D.Store(tmp_path).snapshot()
    full = snap["results"]["full"]
    assert full["locked"] is True and full["n"] == 2
    assert full["by_condition"] == {} and full["paired"] == [] and full["pooled"] is None
    assert snap["progress"]["full"] == {"done": 2, "target": 480}


def test_invalid_action_turn_is_shown_with_the_right_actor(tmp_path):
    _db(tmp_path / "pilot.db", "pilot", {"baseline_v1": [dict(seed=1, outcome="invalid_action", first_mover="B")]})
    store = D.Store(tmp_path)
    [row] = store.snapshot()["negotiations"]
    [event] = store.negotiation(row["key"])["events"]
    # num_rounds=3 with B first -> turns B, A, B.
    assert event == {**event, "turn": 3, "actor": "B", "action": "INVALID", "reason": "illegal_accept"}


def test_transcript_marks_standing_and_accepted_offer(tmp_path):
    _db(tmp_path / "pilot.db", "pilot", {"baseline_v1": [dict(seed=1, outcome="agreed")]})
    store = D.Store(tmp_path)
    offer, accept = store.negotiation(store.snapshot()["negotiations"][0]["key"])["events"]
    assert offer["standing"] is True and offer["value_A"] == 60 and offer["value_B"] == 80
    assert accept["accepted"] == {"turn": 1, "actor": "A"}


def test_wrong_schema_db_is_reported_not_touched(tmp_path):
    bad = tmp_path / "old.db"
    conn = sqlite3.connect(bad)
    conn.execute("CREATE TABLE negotiations (id INTEGER)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    before = _digest(bad)
    snap = D.Store(tmp_path).snapshot()
    [info] = snap["databases"]
    assert info["ok"] is False and "missing tables" in info["error"]
    assert snap["negotiations"] == [] and _digest(bad) == before
