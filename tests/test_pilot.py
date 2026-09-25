"""Pilot driver and budget guard. Offline only: every agent gets a stub
client, and any socket connection attempt fails the test."""
import json
import math
import socket
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.generation import GenerationConfig
from src.experiments.config import APPROVED_OPERATIONAL, APPROVED_PRICES, APPROVED_SCIENTIFIC
from src.experiments.pilot import (PILOT_CELLS, PILOT_SEEDS, pilot_matrix, pilot_worst_case,
                                   prepare_pilot, run_pilot)
from src.negotiation.usage import Budget
from tests.test_agents_stub import claude_resp, openai_resp

CLAUDE_CFG = GenerationConfig(**APPROVED_SCIENTIFIC["ClaudeAgent"], **APPROVED_OPERATIONAL)
OPENAI_CFG = GenerationConfig(**APPROVED_SCIENTIFIC["OpenAIAgent"], **APPROVED_OPERATIONAL)
WALK = {"action_type": "WALK_AWAY", "message": "bye"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail on any non-loopback connection (asyncio's event loop uses a
    loopback socket pair on Windows, so loopback stays allowed)."""
    for name in ("connect", "connect_ex"):
        original = getattr(socket.socket, name)

        def guarded(self, address, _original=original):
            host = address[0] if isinstance(address, tuple) else address
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise AssertionError(f"network access attempted in an offline test: {address}")
            return _original(self, address)
        monkeypatch.setattr(socket.socket, name, guarded)


def test_network_guard_blocks_external_connections():
    with pytest.raises(AssertionError, match="network access"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.1)  # TEST-NET-1, never routed


class Clients:
    """client_factory for the pilot: one endless stub per agent, each
    returning WALK_AWAY with usage (100 in, 20 out). Counts every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, kind):
        async def create(**kwargs):
            self.calls.append((kind, kwargs))
            return claude_resp(tool_input=WALK) if kind == "claude" else openai_resp(content=json.dumps(WALK))
        return SimpleNamespace(messages=SimpleNamespace(create=create),
                               chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def db_rows(db, sql):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


# ------------------------------------------------------------ the matrix
def test_pilot_matrix_is_exactly_the_preregistered_24():
    matrix = pilot_matrix()
    assert len(matrix) == 24
    assert PILOT_SEEDS == (30000, 30001, 30002)
    assert [(c.index, c.condition, c.claude_seat, c.first_mover) for c in PILOT_CELLS] == [
        (1, "baseline_v1", "A", "A"), (2, "baseline_v1", "A", "B"),
        (3, "baseline_v1", "B", "A"), (4, "baseline_v1", "B", "B"),
        (5, "structured_v1", "A", "A"), (6, "structured_v1", "A", "B"),
        (7, "structured_v1", "B", "A"), (8, "structured_v1", "B", "B"),
    ]
    keys = [(c.condition, c.claude_seat, c.first_mover, seed) for c, seed in matrix]
    assert len(set(keys)) == 24                                   # no duplicates
    for seed in PILOT_SEEDS:                                      # all 8 cells per seed
        assert {(c, s, f) for c, s, f, sd in keys if sd == seed} == \
            {(c.condition, c.claude_seat, c.first_mover) for c in PILOT_CELLS}
    assert {sd for *_, sd in keys} == set(PILOT_SEEDS)
    assert not set(PILOT_SEEDS) & set(range(40000, 40060))        # disjoint from full run


@pytest.mark.asyncio
async def test_stubbed_pilot_runs_and_records_the_matrix(tmp_path):
    db = tmp_path / "pilot.db"
    clients = Clients()
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, max_total_tokens=10_000_000,
                             max_cost_usd=100.0, max_input_tokens_per_call=4000,
                             client_factory=clients)
    assert result["status"] == "completed"
    assert [b["negotiations"] for b in result["batches"]] == [3] * 8
    assert len(clients.calls) == 24  # the first mover walks away on turn 1

    runs = db_rows(db, "SELECT * FROM runs ORDER BY started_at")
    negs = db_rows(db, "SELECT n.*, r.first_mover_policy FROM negotiations n "
                       "JOIN runs r USING (run_id) ORDER BY r.started_at, n.negotiation_index")
    assert len(runs) == 8 and all(r["mode"] == "pilot" and r["status"] == "completed" for r in runs)
    assert len(negs) == 24
    got = [(n["method"], "A" if n["model_A"].startswith("claude") else "B", n["first_mover"],
            n["instance_seed"]) for n in negs]
    assert got == [(c.condition, c.claude_seat, c.first_mover, seed) for c, seed in pilot_matrix()]
    for r in runs:
        cfg = json.loads(r["config_json"])
        assert cfg["approved_runtime_enforced"] is True
        assert cfg["experiment"]["budget_max_input_tokens_per_call"] == 4000
        assert {a["generation_config"]["model"] for a in cfg["agents"].values()} == \
            {"claude-sonnet-4-6", "gpt-4.1-2025-04-14"}
        assert {a["params"]["instructions_variant"] for a in cfg["agents"].values()} == {r["method"]}


@pytest.mark.asyncio
async def test_pilot_records_are_distinguishable_from_full_run_records(tmp_path):
    db = tmp_path / "pilot.db"
    await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, max_total_tokens=10_000_000,
                    max_input_tokens_per_call=4000, client_factory=Clients())
    confirmatory = db_rows(db, "SELECT n.* FROM negotiations n JOIN runs r USING (run_id) "
                               "WHERE r.mode = 'full'")
    assert confirmatory == []
    seeds = {n["instance_seed"] for n in db_rows(db, "SELECT instance_seed FROM negotiations")}
    assert seeds == set(PILOT_SEEDS)


# -------------------------------------------------- refusals before any call
BAD_BUDGETS = [
    dict(max_input_tokens_per_call=4000),                                   # no cap at all
    dict(max_total_tokens=100_000),                                         # no input ceiling
    dict(max_total_tokens=0, max_input_tokens_per_call=4000),
    dict(max_total_tokens=-5, max_input_tokens_per_call=4000),
    dict(max_cost_usd=math.nan, max_input_tokens_per_call=4000),
    dict(max_cost_usd=math.inf, max_input_tokens_per_call=4000),
    dict(max_cost_usd=-1.0, max_input_tokens_per_call=4000),
    dict(max_total_tokens=100_000, max_input_tokens_per_call=0),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", BAD_BUDGETS)
async def test_missing_or_invalid_budget_refuses_before_any_call(tmp_path, budget):
    clients, db = Clients(), tmp_path / "pilot.db"
    with pytest.raises(ValueError):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, **budget)
    assert clients.calls == [] and not db.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("prices", [
    {"claude-sonnet-4-6": (3.0, 15.0)},     # no GPT price
    {"gpt-4.1-2025-04-14": (2.0, 8.0)},     # no Claude price
    {}, None,
])
async def test_unknown_model_price_refuses_before_any_call(tmp_path, prices):
    clients, db = Clients(), tmp_path / "pilot.db"
    with pytest.raises(ValueError, match="price"):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, max_total_tokens=10_000_000,
                        max_input_tokens_per_call=4000, prices=prices, client_factory=clients)
    assert clients.calls == [] and not db.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [
    dict(max_total_tokens=1000, max_input_tokens_per_call=4000),  # one call reserves 4 x 5024 tokens
    dict(max_cost_usd=0.01, max_input_tokens_per_call=4000),      # one Claude call reserves $0.2054
])
async def test_budget_too_small_for_one_call_refuses_before_any_call(tmp_path, budget):
    clients, db = Clients(), tmp_path / "pilot.db"
    with pytest.raises(ValueError, match="first call"):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, client_factory=clients, **budget)
    assert clients.calls == [] and not db.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("which,override", [
    ("claude", {"temperature": 0.5}), ("claude", {"effort": "high"}),
    ("openai", {"model": "gpt-4.1"}), ("openai", {"max_retries": 0}),
])
async def test_unapproved_runtime_refuses_before_any_call(tmp_path, which, override):
    claude_cfg = GenerationConfig(**{**CLAUDE_CFG.to_dict(), **(override if which == "claude" else {})})
    openai_cfg = GenerationConfig(**{**OPENAI_CFG.to_dict(), **(override if which == "openai" else {})})
    clients, db = Clients(), tmp_path / "pilot.db"
    with pytest.raises(ValueError, match="approved runtime|price"):
        await run_pilot(claude_cfg, openai_cfg, db, max_total_tokens=10_000_000,
                        max_input_tokens_per_call=4000, client_factory=clients)
    assert clients.calls == [] and not db.exists()


@pytest.mark.asyncio
async def test_existing_database_is_refused(tmp_path):
    db = tmp_path / "pilot.db"
    db.write_bytes(b"")
    with pytest.raises(ValueError, match="already exists"):
        await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, max_total_tokens=10_000_000,
                        max_input_tokens_per_call=4000, client_factory=Clients())


# ---------------------------------------------------- caps during the pilot
# With a 1000-token input ceiling, one call reserves 4 attempts x (1000 + 1024)
# = 8096 tokens; each stub call actually uses 120 tokens.
@pytest.mark.asyncio
async def test_token_cap_stops_the_pilot_before_the_call_that_could_exceed_it(tmp_path):
    db, clients = tmp_path / "pilot.db", Clients()
    # Room for exactly 3 calls: before call n+1, 120*n + 8096 must be <= cap.
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, max_total_tokens=8096 + 2 * 120,
                             max_input_tokens_per_call=1000, client_factory=clients)
    assert result["status"] == "stopped" and len(clients.calls) == 3
    assert [b["negotiations"] for b in result["batches"]] == [3, 0]
    runs = db_rows(db, "SELECT status FROM runs ORDER BY started_at")
    assert [r["status"] for r in runs] == ["completed", "budget_exhausted"]
    (aborted,) = db_rows(db, "SELECT * FROM aborted_negotiations")
    assert aborted["reason"] == "budget_exhausted" and aborted["api_calls"] == 0


@pytest.mark.asyncio
async def test_dollar_cap_stops_the_pilot_before_the_call_that_could_exceed_it(tmp_path):
    db, clients = tmp_path / "pilot.db", Clients()
    # Cell 1: Claude (seat A) moves first and walks: $0.0006 per call. The
    # reserve is the priciest model's worst case: 4 x (1000*$3 + 1024*$15)/1M.
    reserve = 4 * (1000 * 3.0 + 1024 * 15.0) / 1e6
    result = await run_pilot(CLAUDE_CFG, OPENAI_CFG, db, max_cost_usd=reserve + 2 * 0.0006 + 1e-9,
                             max_input_tokens_per_call=1000, client_factory=clients)
    assert result["status"] == "stopped" and len(clients.calls) == 3
    assert result["spent_cost_usd"] == pytest.approx(3 * 0.0006)


def test_pilot_worst_case_uses_the_reserve_and_prices(tmp_path):
    _, budget = prepare_pilot(CLAUDE_CFG, OPENAI_CFG, tmp_path / "p.db", max_cost_usd=1000.0,
                              max_input_tokens_per_call=1000)
    wc = pilot_worst_case(budget)
    assert wc["calls_per_model"] == 24 * 5
    assert wc["worst_case_tokens"] == 2 * 120 * 4 * (1000 + 1024)
    assert wc["worst_case_cost_usd"] == pytest.approx(
        120 * 4 * ((1000 * 3 + 1024 * 15) + (1000 * 2 + 1024 * 8)) / 1e6)


# ------------------------------------------------------------ Budget unit
PRICES = {"m": (3.0, 15.0)}
RESERVE = {"m": (1000, 100, 4)}  # worst case per attempt: 1100 tokens, $0.0045


def test_budget_reserves_the_worst_case_of_the_next_call():
    assert Budget(4400, reserve=RESERVE).exceeded() is None
    assert "reserved" in Budget(4399, reserve=RESERVE).exceeded()
    assert Budget(max_cost_usd=0.018, prices=PRICES, reserve=RESERVE).exceeded() is None
    assert "reserved" in Budget(max_cost_usd=0.0179, prices=PRICES, reserve=RESERVE).exceeded()


def test_budget_charges_known_usage_and_then_refuses_insufficient_remainder():
    b = Budget(4400 + 120, reserve=RESERVE)
    b.add({"model": "m", "input_tokens": 100, "output_tokens": 20, "attempts": 1})
    assert b.spent_tokens == 120 and b.exceeded() is None
    b.add({"model": "m", "input_tokens": 100, "output_tokens": 20, "attempts": 1})
    assert b.spent_tokens == 240 and "reserved" in b.exceeded()


@pytest.mark.parametrize("call,charged_tokens", [
    ({"model": "m", "error": "timeout", "attempts": 4}, 4 * 1100),                      # failed call
    ({"model": "m", "input_tokens": 100, "output_tokens": 20, "attempts": 3}, 120 + 2 * 1100),  # retries
    ({"model": "m", "input_tokens": None, "output_tokens": None, "attempts": 1}, 1100),  # no usage
])
def test_budget_never_counts_unknown_usage_as_zero(call, charged_tokens):
    b = Budget(10**9, max_cost_usd=10**6, prices=PRICES, reserve=RESERVE)
    b.add(call)
    assert b.spent_tokens == charged_tokens
    assert b.spent_cost_usd > 0


def test_known_usage_is_charged_exactly_once():
    b = Budget(10**9, max_cost_usd=10**6, prices=PRICES, reserve=RESERVE)
    call = {"model": "m", "input_tokens": 100, "output_tokens": 20, "attempts": 1}
    b.add(call)
    assert b.spent_tokens == 120                                    # not 240
    assert b.spent_cost_usd == pytest.approx((100 * 3.0 + 20 * 15.0) / 1e6)
    b.add(call)
    assert b.spent_tokens == 240
    assert b.spent_cost_usd == pytest.approx(2 * (100 * 3.0 + 20 * 15.0) / 1e6)


def test_make_budget_builds_the_reserve_for_both_approved_models():
    from src.experiments.pilot import pilot_agents, pilot_config
    from src.experiments.runner import make_budget

    cell = PILOT_CELLS[0]
    config = pilot_config(cell, max_cost_usd=50.0, max_input_tokens_per_call=12345)
    budget = make_budget(config, list(pilot_agents(cell, CLAUDE_CFG, OPENAI_CFG, Clients())),
                         APPROVED_PRICES)
    assert budget.reserve == {
        "claude-sonnet-4-6": (12345, 1024, 1 + 3),
        "gpt-4.1-2025-04-14": (12345, 1024, 1 + 3),
    }
    tokens, cost = budget.next_call_reserve()
    assert tokens == 4 * (12345 + 1024)
    assert cost == pytest.approx(4 * (12345 * 3.0 + 1024 * 15.0) / 1e6)  # Claude is the priciest


def test_budget_stops_on_unknown_usage_it_cannot_bound():
    b = Budget(10**9)  # no reserve
    b.add({"model": "m", "error": "timeout", "attempts": 1})
    assert "usage unknown" in b.exceeded()


def test_budget_stops_when_a_call_exceeds_the_input_ceiling():
    b = Budget(10**9, reserve=RESERVE)
    b.add({"model": "m", "input_tokens": 1001, "output_tokens": 20, "attempts": 1})
    assert "ceiling" in b.exceeded()


def test_budget_dollar_cap_needs_prices_for_reserved_models():
    with pytest.raises(ValueError, match="prices"):
        Budget(max_cost_usd=1.0, prices={}, reserve=RESERVE)


def test_approved_prices_are_the_user_supplied_ones():
    assert APPROVED_PRICES == {"claude-sonnet-4-6": (3.0, 15.0), "gpt-4.1-2025-04-14": (2.0, 8.0)}
