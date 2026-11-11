"""Regression tests for the bugs in docs/qa/hardening-log.md (one test or more per bug ID)."""
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from dashboard import server as SV
from workbench import export, simulations as S, store

ROOT = Path(__file__).resolve().parents[1]
XSS = '<img src=x onerror=alert(1)>'


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "w.db")


def _run(name, agents=("builtin-priority", "builtin-hardliner")):
    sim = S.start({"name": name, "environment": "builtin-field-hospital", "agents": {"X": agents[0], "Y": agents[1]},
                   "conditions": ["baseline_v1"], "repetitions": 1}, background=False)
    return S.get(sim.id)


# H-01: user text in report.md could carry raw HTML, and "|" broke its tables
def test_h01_report_md_never_carries_raw_html_or_breaks_a_table(db):
    from workbench import agents as A, api as WA
    agent, _ = WA._save("agents", A.BUILTIN, A.validate)({"name": f"{XSS} | col", "kind": "priority", "params": {}})
    doc = _run(f"exp {XSS}", (agent["id"], "builtin-hardliner"))
    md = export.build(doc, "report.md")["content"]
    assert "<img" not in md and "&lt;img" in md
    assert export._cell("a | b\nc") == "a \\| b c"


# H-02: an unusable data folder printed a traceback
def test_h02_unusable_data_folder_is_a_plain_message(tmp_path):
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x")
    r = subprocess.run([sys.executable, "-c", "from sandbox.cli import main; main(['--no-browser', '--port', '0'])"],
                       cwd=ROOT, capture_output=True, text=True, timeout=60,
                       env={**__import__("os").environ, "COOPERATION_LAB_HOME": str(blocker), "PYTHONPATH": str(ROOT)})
    assert r.returncode == 2 and "Traceback" not in r.stderr
    assert "could not start" in r.stderr and "COOPERATION_LAB_HOME" in r.stderr


# H-03: on Windows a second copy of the app could bind the same port and share it silently
def test_h03_a_taken_port_cannot_be_shared():
    first = SV.LabServer(("127.0.0.1", 0), partial(SV.Handler, directory=str(SV.STATIC)))
    try:
        with pytest.raises(OSError):
            SV.LabServer(("127.0.0.1", first.server_address[1]), partial(SV.Handler, directory=str(SV.STATIC)))
    finally:
        first.server_close()


def test_h03_an_explicit_taken_port_is_a_plain_message(tmp_path):
    first = SV.LabServer(("127.0.0.1", 0), partial(SV.Handler, directory=str(SV.STATIC)))
    try:
        r = subprocess.run([sys.executable, "-c", f"from sandbox.cli import main; main(['--no-browser', '--port', '{first.server_address[1]}'])"],
                           cwd=ROOT, capture_output=True, text=True, timeout=60,
                           env={**__import__("os").environ, "PYTHONPATH": str(ROOT), "COOPERATION_LAB_HOME": str(tmp_path / "home")})
        assert r.returncode == 2 and "already in use" in r.stderr and "Traceback" not in r.stderr
    finally:
        first.server_close()


# H-04: two long (valid) agent names made the automatic experiment name too long, an error on a field the user never typed
def test_h04_automatic_name_is_shortened_not_refused(db):
    from workbench import agents as A, api as WA
    x, _ = WA._save("agents", A.BUILTIN, A.validate)({"name": "X" * 60, "kind": "priority", "params": {}})
    y, _ = WA._save("agents", A.BUILTIN, A.validate)({"name": "Y" * 60, "kind": "concession", "params": {}})
    cfg = S.validate({"environment": "builtin-field-hospital", "agents": {"X": x["id"], "Y": y["id"]}, "conditions": ["baseline_v1"], "repetitions": 1})
    assert len(cfg["name"]) <= 80 and cfg["name"].endswith("…")
    with pytest.raises(Exception, match="under 80"):
        S.validate({"name": "n" * 81, "environment": "builtin-field-hospital", "agents": {"X": x["id"], "Y": y["id"]}, "conditions": ["baseline_v1"], "repetitions": 1})


# H-05: on Python 3.10 a cell holding "\r" was not quoted, so it could split a CSV row in a spreadsheet
def test_h05_a_line_break_inside_a_cell_stays_inside_the_cell():
    import csv, io
    text = export._csv(["name", "n"], [["two\rlines", 1], ["three\nlines\r\nhere", 2]])
    assert list(csv.reader(io.StringIO(text, newline=""))) == [["name", "n"], ["two\rlines", "1"], ["three\nlines\r\nhere", "2"]]
