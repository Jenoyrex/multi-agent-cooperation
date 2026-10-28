"""Offline simulation pilot: runs the frozen matrix with mock agents at zero
cost, labels every output as simulated, re-checks every record, and refuses
to treat anything else as a simulation."""
import asyncio
import csv
import json
import runpy
import sqlite3
from pathlib import Path

import pytest

from src import zero_cost
from src.experiments import simulation as S
from src.experiments.pilot import pilot_matrix
from src.zero_cost import network_tripwire

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def sim_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("sim") / "simulation.db"
    with network_tripwire() as blocked:
        records = asyncio.run(S.run_simulation(db))
    assert blocked == [] and len(records) == 24
    return db


def copy(db, tmp_path, *statements):
    out = tmp_path / "copy.db"
    out.write_bytes(db.read_bytes())
    conn = sqlite3.connect(out)
    for s in statements:
        conn.execute(s)
    conn.commit()
    conn.close()
    return out


def test_the_simulation_runs_the_frozen_matrix_and_every_record_rechecks(sim_db):
    rows = S.load_rows(sim_db)
    assert S.verify(rows) == []
    assert sorted((S.cell_of(r), r["instance_seed"]) for r in rows) == sorted((c.index, s) for c, s in pilot_matrix())
    assert {r["mode"] for r in rows} == {"simulation"} and {r["api_calls"] for r in rows} == {0}
    assert {r["model_A"] for r in rows} == {"SIMULATED-mock-X", "SIMULATED-mock-Y"}
    summary = S.analyse(rows)
    assert summary["condition_has_no_effect_by_construction"] is True   # mocks ignore instructions
    for v in summary["by_condition"].values():                         # RWE/EW use every negotiation
        assert v["negotiations"] == 12 and sum(v["outcomes"].values()) == 12


def test_artifacts_are_labelled_and_byte_identical_across_runs(sim_db, tmp_path):
    rows = S.load_rows(sim_db)
    a = S.write_artifacts(tmp_path / "a", S.analyse(rows), S.verify(rows), [])
    b = S.write_artifacts(tmp_path / "b", S.analyse(rows), S.verify(rows), [])
    assert a == b
    report = (tmp_path / "a" / "REPORT.md").read_text(encoding="utf-8")
    for must in ("SIMULATED DATA", "No empirical experiment with a real LLM has been conducted",
                 "empirically unanswered", "## 4. Empirical LLM results\n**None.**", "No inferential test was run"):
        assert must in report
    with open(tmp_path / "a" / "negotiations.csv", encoding="utf-8") as f:
        assert {r["data_label"] for r in csv.DictReader(f)} == {"SIMULATED"}
    summary = json.loads((tmp_path / "a" / "summary.json").read_text(encoding="utf-8"))
    assert summary["data_label"] == S.LABEL and summary["integrity_problems"] == []


@pytest.mark.parametrize("statement,found", [
    ("UPDATE runs SET mode = 'pilot'", "pilot"),
    ("UPDATE negotiations SET model_A = 'claude:claude-sonnet-4-6' WHERE id = 1", "claude:claude-sonnet-4-6"),
])
def test_anything_but_simulated_data_is_refused(sim_db, tmp_path, statement, found):
    with pytest.raises(ValueError, match=f"not simulated mock-agent data.*{found}"):
        S.load_rows(copy(sim_db, tmp_path, statement))


@pytest.mark.parametrize("statement,problem", [
    ("UPDATE negotiations SET optimal_welfare = optimal_welfare + 1 WHERE id = 1", "optimal welfare"),
    ("UPDATE negotiations SET utility_A = utility_A + 1, total_welfare = total_welfare + 1 WHERE id = 1", "utilities"),
    ("UPDATE negotiations SET valuation_A_json = '{}' WHERE id = 1", "instance differs"),
    ("DELETE FROM negotiations WHERE id = 1", "matrix"),
    ("UPDATE negotiations SET api_calls = 1 WHERE id = 1", "API call"),
])
def test_the_rechecks_catch_tampered_records(sim_db, tmp_path, statement, problem):
    problems = S.verify(S.load_rows(copy(sim_db, tmp_path, statement)))
    assert any(problem in p for p in problems), problems


def test_the_zero_difference_check_is_not_vacuous(sim_db, tmp_path):
    db = copy(sim_db, tmp_path, "UPDATE negotiations SET utility_A = utility_A + 5, total_welfare = total_welfare + 5 "
                                "WHERE id = (SELECT MIN(id) FROM negotiations WHERE method = 'structured_v1')")
    assert S.analyse(S.load_rows(db))["condition_has_no_effect_by_construction"] is False


def test_the_script_refuses_to_run_with_paid_calls_unlocked(monkeypatch, tmp_path):
    monkeypatch.setenv(zero_cost.ENV, "1")
    monkeypatch.setattr("sys.argv", ["run_offline_simulation.py", "--out", str(tmp_path)])
    script = runpy.run_path(str(ROOT / "scripts" / "run_offline_simulation.py"), run_name="not_main")
    with pytest.raises(SystemExit, match="zero-cost mode"):
        script["main"]()
    assert list(tmp_path.iterdir()) == []


def test_the_script_never_deletes_a_database_that_is_not_a_simulation(sim_db, monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    foreign = copy(sim_db, tmp_path, "UPDATE runs SET mode = 'pilot'")
    (out / "simulation.db").write_bytes(foreign.read_bytes())
    monkeypatch.setattr("sys.argv", ["run_offline_simulation.py", "--out", str(out)])
    script = runpy.run_path(str(ROOT / "scripts" / "run_offline_simulation.py"), run_name="not_main")
    with pytest.raises(ValueError, match="not simulated"):
        script["main"]()
    assert (out / "simulation.db").read_bytes() == foreign.read_bytes()


def test_the_script_writes_the_committed_artifacts(monkeypatch, tmp_path):
    """The committed artifacts are exactly what the script produces now."""
    monkeypatch.setattr("sys.argv", ["run_offline_simulation.py", "--out", str(tmp_path)])
    script = runpy.run_path(str(ROOT / "scripts" / "run_offline_simulation.py"), run_name="not_main")
    assert script["main"]() == 0
    for name in ("negotiations.csv", "summary.json", "REPORT.md"):
        assert (tmp_path / name).read_bytes() == (ROOT / "artifacts" / "offline_simulation" / name).read_bytes()
