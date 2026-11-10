"""The 480-negotiation full run (§8.3). Offline only: stub clients, no network."""
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from dashboard import data as D
from scripts import run_analysis
from src.environment.resources import DEFAULT_CATEGORY_NAMES
from src.evaluation.inference import analyse
from src.experiments.pilot import (FULL, PILOT, PILOT_CELLS, Design, _remaining, pilot_matrix, prepare_pilot,
                                   run_pilot)
from src.zero_cost import ZeroCostViolation
from tests.test_agents_stub import ApiError, claude_resp, openai_resp
from tests.test_pilot import CLAUDE_CFG, OPENAI_CFG, Clients, db_rows, no_network  # noqa: F401
from tests.test_pilot_resume import Faulty, no_backoff  # noqa: F401

CAPS = dict(max_total_tokens=10**9, max_input_tokens_per_call=12000)
ONE_SEED = Design("full", "full run", (40000,))
CATS = DEFAULT_CATEGORY_NAMES[:3]


class Bargainers:
    """client_factory: the opener offers nothing for itself and the other side
    accepts, except on baseline negotiations whose prompt length is odd, which
    walk away, so the two conditions really differ."""

    def __call__(self, kind):
        async def create(**kwargs):
            prompt = json.dumps(kwargs)
            if "moving first" not in prompt:
                action = {"action_type": "ACCEPT", "message": "ok"}
            elif "Preference" not in prompt and len(prompt) % 2:
                action = {"action_type": "WALK_AWAY", "message": "no"}
            else:
                action = {"action_type": "OFFER", "allocation": {c: 0 for c in CATS}, "message": "all yours"}
            return claude_resp(tool_input=action) if kind == "claude" else openai_resp(content=json.dumps(action))
        return SimpleNamespace(messages=SimpleNamespace(create=create),
                               chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def run(db, clients, design=FULL, **kw):
    return run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, design=design,
                     confirmed_full_run=True, **CAPS, **kw)


def test_full_design_is_60_seeds_by_8_cells_run_instance_by_instance():
    assert len(pilot_matrix(FULL)) == len(set(pilot_matrix(FULL))) == 480
    plan = _remaining({}, FULL)
    assert [(c.index, s) for c, s, _ in plan[:9]] == [*((c.index, 40000) for c in PILOT_CELLS), (1, 40001)]
    assert {n for *_, n in plan} == {1} and plan[-1][1] == 40059


async def test_all_480_stored_design_complete_and_analysable(tmp_path, capsys, monkeypatch):
    db = tmp_path / "full.db"
    result = await run(db, Bargainers())
    assert (result["status"], result["completed_negotiations"], result["blocked"]) == ("completed", 480, [])

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [D.summarize(D.row_to_record(r), str(r["id"]), "full") for r in conn.execute(
        "SELECT n.* FROM negotiations n JOIN runs r USING (run_id) WHERE r.mode = 'full'")]
    conn.close()
    assert len(rows) == 480 and D.full_design_complete(rows)
    assert {r["outcome"] for r in rows} == {"agreed", "walked_away"}
    assert set(analyse(rows)["primary"]) == {"H1", "H2", "H3"}

    monkeypatch.setattr(sys, "argv", ["run_analysis", "--db", str(db)])
    assert run_analysis.main() == 0
    assert "H1" in capsys.readouterr().out

    plan, _ = prepare_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=Bargainers(), resume=True, design=FULL, **CAPS)
    assert plan == []


async def test_nothing_runs_without_confirmation(tmp_path):
    clients = Clients()
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, tmp_path / "full.db", client_factory=clients, design=ONE_SEED, **CAPS)
    assert (result["status"], clients.calls) == ("stopped", [])


async def test_real_clients_are_refused_in_zero_cost_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_PAID_PROVIDER_CALLS", raising=False)
    with pytest.raises(ZeroCostViolation, match="full run"):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, tmp_path / "full.db", design=FULL, confirmed_full_run=True, **CAPS)
    assert list(tmp_path.iterdir()) == []


async def test_blocked_pair_needs_retry_blocked_then_completes(tmp_path, no_backoff):  # noqa: F811
    db = tmp_path / "full.db"
    # 3 clean attempts x 4 in-turn tries fail on the first pair; everything else walks away.
    failing = Faulty(lambda n: ApiError(500) if n <= 12 else None)
    first = await run(db, failing, ONE_SEED)
    assert first["status"] == "stopped" and first["blocked"] == [(1, 40000)]

    skipped = await run(db, Faulty(), ONE_SEED, resume=True)
    assert skipped["status"] == "incomplete" and skipped["completed_negotiations"] == 7

    fixed = Faulty()
    done = await run(db, fixed, ONE_SEED, resume=True, retry_blocked=True)
    assert (done["status"], done["completed_negotiations"], done["blocked"], len(fixed.calls)) == ("completed", 8, [], 1)
    assert db_rows(db, "SELECT COUNT(*) n FROM negotiations") == [{"n": 8}]


async def test_a_budget_stop_resumes_on_the_same_budget(tmp_path):
    db = tmp_path / "full.db"
    # One call reserves 4 x (12000 + 1024) tokens and really costs 120: room for 4 calls.
    caps = dict(max_total_tokens=4 * (12000 + 1024) + 3 * 120, max_input_tokens_per_call=12000)
    first = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=Clients(), design=ONE_SEED,
                            confirmed_full_run=True, **caps)
    assert first["status"] == "stopped" and first["completed_negotiations"] == 4
    with pytest.raises(ValueError, match="cannot cover"):  # the spend survives the restart
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=Clients(), design=ONE_SEED, resume=True,
                        confirmed_full_run=True, **caps)
    with pytest.raises(ValueError, match="same caps"):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=Clients(), design=ONE_SEED, resume=True,
                        confirmed_full_run=True, max_total_tokens=10**9, max_input_tokens_per_call=12000)


async def test_pilot_and_full_databases_can_share_a_folder_but_not_mix(tmp_path):
    pilot_db, full_db = tmp_path / "pilot.db", tmp_path / "full.db"
    await run_pilot(CLAUDE_CFG, OPENAI_CFG, pilot_db, client_factory=Clients(), **CAPS)
    assert (await run(full_db, Clients(), ONE_SEED))["completed_negotiations"] == 8
    with pytest.raises(ValueError, match="existing full run database"):
        prepare_pilot(CLAUDE_CFG, OPENAI_CFG, pilot_db, client_factory=Clients(), resume=True, design=FULL, **CAPS)
    with pytest.raises(ValueError, match="already exist"):
        await run(tmp_path / "second.db", Clients(), ONE_SEED)
    assert PILOT.seeds == (30000, 30001, 30002)
