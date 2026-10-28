"""The pilot's one budget survives restarts (F1): it is kept in the pilot
database, a resume replays it, and only (cell, seed) pairs without a record
are run again. Offline only: stub clients, and any non-loopback socket
connection fails the test (no_network, from test_pilot)."""
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.experiments import pilot as P
from src.experiments.pilot import pilot_matrix, prepare_pilot, run_pilot
from tests.test_agents_stub import ApiError, claude_resp, openai_resp
from tests.test_pilot import CLAUDE_CFG, OPENAI_CFG, WALK, db_rows, no_network  # noqa: F401

# One stub call: 100 input + 20 output tokens, and the first mover walks away,
# so every negotiation is exactly one call. With a 1000-token input ceiling one
# attempt reserves 1000 + 1024 tokens, one call 4 attempts.
CALL, ATTEMPT = 120, 1000 + 1024
RESERVE = 4 * ATTEMPT
CAPS = dict(max_total_tokens=10_000_000, max_input_tokens_per_call=1000)


class Faulty:
    """client_factory whose n-th create() call (1-based, across both models)
    raises fail(n) if that is not None, else walks away."""

    def __init__(self, fail=lambda n: None):
        self.calls, self.fail = [], fail

    def __call__(self, kind):
        async def create(**kwargs):
            self.calls.append(kind)
            exc = self.fail(len(self.calls))
            if exc is not None:
                raise exc
            return claude_resp(tool_input=WALK) if kind == "claude" else openai_resp(content=json.dumps(WALK))
        return SimpleNamespace(messages=SimpleNamespace(create=create),
                               chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.fixture
def no_backoff(monkeypatch):
    async def instant(_):
        return None
    monkeypatch.setattr("src.agents.generation.asyncio.sleep", instant)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pairs(db):
    """(cell index, seed) of every stored negotiation."""
    cells = {(c.condition, c.claude_seat, c.first_mover): c.index for c in P.PILOT_CELLS}
    return [(cells[(n["method"], "A" if n["model_A"].startswith("claude") else "B", n["first_mover"])],
             n["instance_seed"]) for n in db_rows(db, "SELECT * FROM negotiations")]


ALL_PAIRS = sorted((c.index, s) for c, s in pilot_matrix())


@pytest.mark.asyncio
async def test_a_restart_can_neither_reset_nor_raise_the_budget(tmp_path):
    db, clients = tmp_path / "pilot.db", Faulty()
    caps = dict(max_total_tokens=RESERVE + 2 * CALL, max_input_tokens_per_call=1000)  # room for 3 calls
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, **caps)
    assert result["status"] == "stopped" and len(clients.calls) == 3
    assert result["spent_tokens"] == 3 * CALL
    before = digest(db)

    refusals = [
        (dict(resume=True, **caps), "first call"),                     # same caps: still exhausted
        (dict(resume=True, max_total_tokens=10_000_000, max_input_tokens_per_call=1000), "same caps"),
        (dict(**caps), "already exists"),                               # fresh over it
    ]
    for kwargs, match in refusals:
        with pytest.raises(ValueError, match=match):
            await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, **kwargs)
    with pytest.raises(ValueError, match="already exist"):             # fresh beside it
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, tmp_path / "pilot2.db", client_factory=clients, **CAPS)
    with pytest.raises(ValueError, match="no pilot to resume"):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, tmp_path / "x" / "none.db", client_factory=clients,
                        resume=True, **CAPS)
    assert len(clients.calls) == 3 and digest(db) == before
    assert not (tmp_path / "pilot2.db").exists()


@pytest.mark.asyncio
async def test_resume_after_a_crash_runs_only_the_missing_pairs_and_charges_the_lost_call(tmp_path):
    db = tmp_path / "pilot.db"
    crash = Faulty(lambda n: RuntimeError("process died") if n == 5 else None)
    with pytest.raises(RuntimeError, match="process died"):  # cell 2, seed 30001, mid-call
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=crash, **CAPS)
    assert [r["status"] for r in db_rows(db, "SELECT status FROM runs ORDER BY started_at")] == \
        ["completed", "failed"]
    assert sorted(pairs(db)) == [(1, 30000), (1, 30001), (1, 30002), (2, 30000)]

    # The dry run of the resume reads only: the remaining plan and the spend so far,
    # including one worst-case call for the call lost in the crash.
    before = digest(db)
    plan, budget = prepare_pilot(CLAUDE_CFG, OPENAI_CFG, db, resume=True, **CAPS)
    assert [(c.index, cfg.base_seed, cfg.num_negotiations) for c, cfg, *_ in plan] == \
        [(2, 30001, 2)] + [(i, 30000, 3) for i in range(3, 9)]
    assert budget.spent_tokens == 4 * CALL + RESERVE
    assert digest(db) == before

    clients = Faulty()
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, resume=True, **CAPS)
    assert result["status"] == "completed" and result["completed_negotiations"] == 24
    assert len(clients.calls) == 20                      # nothing already recorded is run again
    assert sorted(pairs(db)) == ALL_PAIRS                # every pair exactly once
    assert result["spent_tokens"] == 24 * CALL + RESERVE
    assert len(db_rows(db, "SELECT id FROM budget_ledger")) == 24


@pytest.mark.asyncio
async def test_in_turn_retries_are_charged_once_persisted_and_replayed(tmp_path, no_backoff):
    db, clients = tmp_path / "pilot.db", Faulty(lambda n: ApiError(500) if n == 1 else None)
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, **CAPS)
    assert result["status"] == "completed" and len(clients.calls) == 25
    # The discarded attempt's usage is unknown: charged one attempt's worst case.
    assert result["spent_tokens"] == 24 * CALL + ATTEMPT

    plan, budget = prepare_pilot(CLAUDE_CFG, OPENAI_CFG, db, resume=True, **CAPS)
    assert plan == []
    assert (budget.spent_tokens, budget.spent_cost_usd) == pytest.approx(
        (result["spent_tokens"], result["spent_cost_usd"]))

    again = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, resume=True, **CAPS)
    assert again["status"] == "completed" and again["batches"] == [] and len(clients.calls) == 25


@pytest.mark.asyncio
async def test_exhausted_transport_reruns_block_that_pair_and_a_resume_skips_it(tmp_path, no_backoff):
    db = tmp_path / "pilot.db"
    # Calls 4-15: cell 2 / seed 30000 fails all 4 in-turn attempts in each of its 3 runs.
    failing = Faulty(lambda n: ApiError(500) if 4 <= n <= 15 else None)
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=failing, **CAPS)
    assert result["status"] == "stopped" and result["blocked"] == [(2, 30000)]
    assert [b["negotiations"] for b in result["batches"]] == [3, 2]
    assert [(a["reason"], a["attempt_number"], a["will_rerun"]) for a in
            db_rows(db, "SELECT * FROM aborted_negotiations ORDER BY id")] == \
        [("transport_failure", 1, 1), ("transport_failure", 2, 1), ("transport_failure", 3, 0)]

    clients = Faulty()
    resumed = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, resume=True, **CAPS)
    assert resumed["status"] == "incomplete" and resumed["blocked"] == [(2, 30000)]
    assert resumed["completed_negotiations"] == 23 and len(clients.calls) == 18
    assert sorted(pairs(db)) == [p for p in ALL_PAIRS if p != (2, 30000)]
    assert resumed["spent_tokens"] == 23 * CALL + 12 * ATTEMPT   # every failed attempt charged


@pytest.mark.asyncio
async def test_a_second_concurrent_invocation_is_refused(tmp_path):
    db, clients = tmp_path / "pilot.db", Faulty()
    with P._single_invocation(db):
        with pytest.raises(ValueError, match="Another pilot invocation"):
            await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, **CAPS)
    assert clients.calls == [] and not db.exists()


DUPLICATE = """
CREATE TEMP TABLE r AS SELECT * FROM runs WHERE run_id = :run; UPDATE r SET run_id = 'dup';
INSERT INTO runs SELECT * FROM r;
CREATE TEMP TABLE n AS SELECT * FROM negotiations WHERE run_id = :run AND negotiation_index = 0;
UPDATE n SET run_id = 'dup', id = NULL; INSERT INTO negotiations SELECT * FROM n;
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper,match", [
    (DUPLICATE, "more than once"),
    ("UPDATE runs SET prompt_hash = 'other' WHERE run_id = :run;", "prompt_hash"),
    ("UPDATE runs SET mode = 'full' WHERE run_id = :run;", "mode=full"),
    ("UPDATE budget_ledger SET charged_tokens = charged_tokens - 1 WHERE id = 1;", "ledger row 1"),
])
async def test_a_resume_refuses_duplicated_or_foreign_records(tmp_path, tamper, match):
    db = tmp_path / "pilot.db"
    await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=Faulty(), **CAPS)
    conn = sqlite3.connect(db)
    run = conn.execute("SELECT run_id FROM runs ORDER BY started_at").fetchone()[0]
    for statement in filter(str.strip, tamper.split(";")):
        conn.execute(statement, {"run": run} if ":run" in statement else {})
    conn.commit()
    conn.close()
    clients = Faulty()
    with pytest.raises(ValueError, match=match):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, resume=True, **CAPS)
    assert clients.calls == []


@pytest.mark.asyncio
async def test_an_input_ceiling_breach_stops_the_pilot_and_survives_a_restart(tmp_path):
    """F2: input size is only known after a call, so a breach is caught right
    after it; it must stop all further calls, including after a resume."""
    db = tmp_path / "pilot.db"

    class Oversized(Faulty):
        def __call__(self, kind):
            async def create(**kwargs):
                self.calls.append(kind)
                return claude_resp(tool_input=WALK, usage=(1500, 20))  # 1500 > 1000 ceiling
            return SimpleNamespace(messages=SimpleNamespace(create=create))

    clients = Oversized()
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, **CAPS)
    assert result["status"] == "stopped" and len(clients.calls) == 1
    with pytest.raises(ValueError, match="input tokens, above the reserved ceiling"):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, resume=True, **CAPS)
    assert len(clients.calls) == 1
