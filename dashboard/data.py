"""Read-only data access for the dashboard.

Presentation layer only: every number is computed by the frozen research
code (src.evaluation.metrics, src.environment.allocation) from rows the
runner stored. Databases are opened with SQLite's read-only URI mode, so
the dashboard can never write to, migrate or create an experiment database
(src.storage.db.get_connection would, so it is deliberately not used here).

Preregistration guard (docs/spec.md §8.9, "No interim stopping"): full-run
outcomes are not compared by condition until all 480 full-run negotiations
are stored. Until then full-run results are locked and only progress is
shown. Pilot data is operational (§8.4) and is always labelled as such.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from src.agents.prompting import BASELINE_VARIANT, STRUCTURED_V1_BLOCK, SYSTEM_INSTRUCTIONS
from src.environment.allocation import compute_utility
from src.evaluation import metrics as M
from src.experiments import provenance
from src.experiments.config import (APPROVED_CLAUDE_THINKING, APPROVED_MAX_ROUNDS,
                                    APPROVED_MAX_TRANSPORT_RERUNS, APPROVED_OPERATIONAL,
                                    APPROVED_PRICES, APPROVED_SCIENTIFIC)
from src.experiments.pilot import CONDITIONS, PILOT_CELLS, PILOT_SEEDS, pilot_matrix
from src.negotiation.protocol import NegotiationRecord
from src.storage.db import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"

# Full-run design, docs/spec.md §8.3. There is no full-run driver in code yet,
# so these are display constants copied from the preregistration.
FULL_RUN_SEEDS = (40000, 40059)
FULL_RUN_INSTANCES = 60
FULL_RUN_TOTAL = 480
PILOT_TOTAL = len(pilot_matrix())
NEG_PER_INSTANCE_PER_CONDITION = 4  # 2 seats x 2 first movers (§8.9)

TABLES = ("runs", "negotiations", "aborted_negotiations")


# ---------------------------------------------------------------- database

def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def inspect_db(path: Path) -> dict:
    """Schema check without writing: {'ok': bool, 'error': str|None, ...}."""
    info = {"name": path.stem, "path": str(path), "ok": False, "error": None}
    try:
        conn = open_readonly(path)
    except sqlite3.Error as exc:
        return {**info, "error": f"cannot open: {exc}"}
    try:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        missing = [t for t in TABLES if t not in present]
        if missing:
            info["error"] = f"missing tables {missing}"
        elif version != SCHEMA_VERSION:
            info["error"] = f"schema version {version}, expected {SCHEMA_VERSION}"
        else:
            info["ok"] = True
    except sqlite3.Error as exc:
        info["error"] = str(exc)
    finally:
        conn.close()
    return info


def discover(results_dir: Path = RESULTS_DIR) -> list[Path]:
    return sorted(results_dir.glob("*.db")) if results_dir.is_dir() else []


# ---------------------------------------------------------------- records

def family(model_name: str) -> str:
    """'claude:claude-sonnet-4-6' -> 'claude'; mock names pass through."""
    return model_name.split(":", 1)[0] if ":" in model_name else model_name


def row_to_record(row: sqlite3.Row) -> NegotiationRecord:
    return NegotiationRecord(
        instance_seed=row["instance_seed"], method=row["method"], model_A=row["model_A"],
        model_B=row["model_B"], first_mover=row["first_mover"], outcome=row["outcome"],
        num_rounds=row["num_rounds"], final_allocation=json.loads(row["final_allocation_json"]),
        utility_A=row["utility_A"], utility_B=row["utility_B"], total_welfare=row["total_welfare"],
        optimal_welfare=row["optimal_welfare"], resource_pool=json.loads(row["resource_pool_json"]),
        valuation_A=json.loads(row["valuation_A_json"]), valuation_B=json.loads(row["valuation_B_json"]),
        invalid_reason=row["invalid_reason"], invalid_detail=row["invalid_detail"],
        first_mover_policy=row["first_mover_policy"], run_id=row["run_id"],
        negotiation_index=row["negotiation_index"], max_rounds=row["max_rounds"],
    )


def claude_vs_gpt(record: NegotiationRecord) -> Optional[float]:
    """Model imbalance u_Claude/100 - u_GPT/100 (§8.6); None unless the pair
    is exactly one Claude and one OpenAI model, or not agreed."""
    by_family = {family(record.model_A): record.model_A, family(record.model_B): record.model_B}
    if set(by_family) != {"claude", "openai"}:
        return None
    return M.model_imbalance(record, by_family["claude"], by_family["openai"])


def summarize(record: NegotiationRecord, key: str, mode: str) -> dict:
    envy = M.envy_freeness(record)
    fam_A, fam_B = family(record.model_A), family(record.model_B)
    return {
        "key": key, "mode": mode, "run_id": record.run_id, "index": record.negotiation_index,
        "seed": record.instance_seed, "condition": record.method,
        "model_A": record.model_A, "model_B": record.model_B, "family_A": fam_A, "family_B": fam_B,
        "claude_seat": "A" if fam_A == "claude" else "B" if fam_B == "claude" else None,
        "first_mover": record.first_mover, "outcome": record.outcome,
        "invalid_reason": record.invalid_reason, "rounds": record.num_rounds,
        "max_rounds": record.max_rounds, "utility_A": record.utility_A, "utility_B": record.utility_B,
        "optimal_welfare": record.optimal_welfare,
        # unconditional (failures score 0)
        "rwe": M.relative_welfare_efficiency(record), "ew": M.egalitarian_welfare(record),
        # agreement-only (None when not agreed)
        "sw": M.social_welfare(record), "swe": M.social_welfare_efficiency(record),
        "equitability": M.equitability(record),
        "envious_A": envy.envious_A if envy else None, "envious_B": envy.envious_B if envy else None,
        "envy_free": envy.envy_free if envy else None,
        "imbalance": claude_vs_gpt(record), "first_mover_gap": M.first_mover_gap(record),
    }


# ---------------------------------------------------------------- aggregation

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def aggregate(rows: list[dict], records: list[NegotiationRecord]) -> Optional[dict]:
    """Condition-level summary. Outcome rates and the unconditional
    confirmatory metrics use all n (via summarize_batch); agreement-only
    metrics report their own n_agreed."""
    if not records:
        return None
    b = M.summarize_batch(records)
    agreed = [r for r in rows if r["outcome"] == "agreed"]
    return {
        "n": b.n, "n_agreed": len(agreed),
        "agreement_rate": b.agreement_rate, "rwe": b.mean_rwe, "ew": b.mean_egalitarian_welfare,
        "swe": b.mean_swe, "equitability": b.mean_equitability, "envy_free_rate": b.envy_free_rate,
        "envious_A_rate": _mean([float(r["envious_A"]) for r in agreed]),
        "envious_B_rate": _mean([float(r["envious_B"]) for r in agreed]),
        "imbalance": _mean([r["imbalance"] for r in agreed]),
        "n_imbalance": sum(r["imbalance"] is not None for r in agreed),
        "first_mover_gap": _mean([r["first_mover_gap"] for r in agreed]),
        "mean_rounds_to_agreement": b.mean_rounds_to_agreement,
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "invalid_reasons": dict(Counter(r["invalid_reason"] for r in rows if r["invalid_reason"])),
        "invalid_action_rate": b.invalid_action_rate,
        "rounds": dict(Counter(r["rounds"] for r in rows)),
        "outcomes_by_first_mover": {
            fm: dict(Counter(r["outcome"] for r in rows if r["first_mover"] == fm)) for fm in "AB"},
    }


def paired_differences(rows: list[dict]) -> list[dict]:
    """§8.9 per-instance paired differences d_i = m_structured - m_baseline,
    where m is the mean over that condition's negotiations of the instance.
    Descriptive only: the preregistered tests/CIs are not computed here."""
    by_instance = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_instance[r["seed"]][r["condition"]].append(r)
    out = []
    for seed in sorted(by_instance):
        cond = by_instance[seed]
        base, struct = cond.get(BASELINE_VARIANT, []), cond.get("structured_v1", [])
        if not base or not struct:
            continue
        entry = {"seed": seed, "n_baseline": len(base), "n_structured": len(struct)}
        for m in ("rwe", "ew"):
            entry[m] = _mean([r[m] for r in struct]) - _mean([r[m] for r in base])
        entry["agreement_rate"] = (_mean([float(r["outcome"] == "agreed") for r in struct])
                                   - _mean([float(r["outcome"] == "agreed") for r in base]))
        out.append(entry)
    return out


def dataset_view(rows: list[dict], records: list[NegotiationRecord], mode: str) -> dict:
    """Results for one dataset (pilot or full), respecting the §8.9 lock."""
    target = PILOT_TOTAL if mode == "pilot" else FULL_RUN_TOTAL
    view = {"mode": mode, "n": len(rows), "target": target, "locked": False, "by_condition": {},
            "paired": [], "pooled": aggregate(rows, records)}
    if mode == "full" and len(rows) < FULL_RUN_TOTAL:
        # Not even pooled outcomes: only progress is shown before completion.
        view.update(locked=True, pooled=None)
        return view
    for c in CONDITIONS:
        idx = [i for i, r in enumerate(rows) if r["condition"] == c]
        view["by_condition"][c] = aggregate([rows[i] for i in idx], [records[i] for i in idx])
    view["paired"] = paired_differences(rows)
    return view


# ---------------------------------------------------------------- detail

def _actor_at(first_mover: str, turn: int) -> str:
    other = "B" if first_mover == "A" else "A"
    return first_mover if turn % 2 == 1 else other


def transcript_events(row: sqlite3.Row, record: NegotiationRecord) -> list[dict]:
    """The stored public transcript, turn by turn, annotated with the
    standing offer and the evaluator-side value of each offer (computed from
    the stored hidden valuations; agents never saw these)."""
    calls = {(c.get("turn_number"), c.get("actor")): c for c in json.loads(row["calls_json"])}
    events, standing = [], None
    for t in json.loads(row["transcript_json"]):
        a, turn, actor = t["action"], t["turn_number"], t["actor"]
        ev = {"turn": turn, "actor": actor, "action": a["action_type"], "message": a["message"],
              "allocation": a["allocation"], "value_A": None, "value_B": None,
              "call": _call_view(calls.get((turn, actor)))}
        if a["action_type"] == "OFFER" and a["allocation"]:
            ev["value_A"] = compute_utility(a["allocation"]["A"], record.valuation_A)
            ev["value_B"] = compute_utility(a["allocation"]["B"], record.valuation_B)
            final = turn == record.max_rounds
            ev["standing"] = not final
            ev["note"] = "Final-turn offer: never standing (deadline → timeout)" if final else None
            if not final:
                standing = {"turn": turn, "actor": actor}
        elif a["action_type"] == "ACCEPT":
            ev["accepted"] = standing
        ev["standing_after"] = standing if a["action_type"] == "OFFER" else None
        events.append(ev)
    if record.outcome == "invalid_action":
        # The faulty turn is never appended to the transcript by the engine.
        turn = record.num_rounds
        actor = _actor_at(record.first_mover, turn)
        events.append({"turn": turn, "actor": actor, "action": "INVALID", "message": None,
                       "allocation": None, "reason": record.invalid_reason,
                       "detail": record.invalid_detail, "call": _call_view(calls.get((turn, actor)))})
    return events


def _call_view(call: Optional[dict]) -> Optional[dict]:
    if not call:
        return None
    keep = ("model", "response_model", "stop_reason", "input_tokens", "output_tokens",
            "latency_s", "attempts", "error", "raw_output")
    return {k: call.get(k) for k in keep}


# ---------------------------------------------------------------- facade

class Store:
    """Everything the dashboard shows, read from `results_dir` on demand."""

    def __init__(self, results_dir: Path = RESULTS_DIR, paths: Optional[list[Path]] = None):
        self.results_dir = results_dir
        self.paths = paths

    def _dbs(self) -> list[dict]:
        return [inspect_db(p) for p in (self.paths if self.paths is not None else discover(self.results_dir))]

    def _load(self):
        dbs, runs, rows, records, aborted = self._dbs(), [], [], [], []
        for db in dbs:
            if not db["ok"]:
                continue
            conn = open_readonly(Path(db["path"]))
            try:
                modes = {}
                for r in conn.execute("SELECT * FROM runs ORDER BY started_at"):
                    run = dict(r)
                    run["config"] = json.loads(run.pop("config_json"))
                    run["totals"] = json.loads(run.pop("totals_json") or "null")
                    run["dependency_versions"] = json.loads(run.pop("dependency_versions_json"))
                    run["db"] = db["name"]
                    modes[run["run_id"]] = run["mode"]
                    runs.append(run)
                for r in conn.execute("SELECT * FROM negotiations ORDER BY id"):
                    rec = row_to_record(r)
                    rows.append(summarize(rec, f"{db['name']}:{r['id']}", modes.get(r["run_id"], "unknown")))
                    records.append(rec)
                for r in conn.execute("SELECT * FROM aborted_negotiations ORDER BY timestamp"):
                    a = {k: r[k] for k in r.keys() if k not in ("transcript_json", "calls_json")}
                    a["db"], a["mode"] = db["name"], modes.get(r["run_id"], "unknown")
                    aborted.append(a)
            finally:
                conn.close()
        return dbs, runs, rows, records, aborted

    def snapshot(self) -> dict:
        dbs, runs, rows, records, aborted = self._load()
        by_mode = lambda m: ([r for r in rows if r["mode"] == m],
                             [rec for r, rec in zip(rows, records) if r["mode"] == m])
        results = {m: dataset_view(*by_mode(m), m) for m in ("pilot", "full")}
        counts = Counter(r["mode"] for r in rows)
        return {
            "provenance": current_provenance(),
            "databases": dbs,
            "runs": runs,
            "aborted": aborted,
            "negotiations": rows,
            "results": results,
            "progress": {
                "pilot": {"done": counts["pilot"], "target": PILOT_TOTAL},
                "full": {"done": counts["full"], "target": FULL_RUN_TOTAL},
                "other": {m: n for m, n in counts.items() if m not in ("pilot", "full")},
                "aborted_attempts": len(aborted),
                "last_run": max(runs, key=lambda r: r["started_at"])["run_id"] if runs else None,
            },
            "setup": setup(),
        }

    def negotiation(self, key: str) -> Optional[dict]:
        name, _, ident = key.partition(":")
        db = next((d for d in self._dbs() if d["name"] == name and d["ok"]), None)
        if db is None or not ident.isdigit():
            return None
        conn = open_readonly(Path(db["path"]))
        try:
            row = conn.execute("SELECT * FROM negotiations WHERE id = ?", (int(ident),)).fetchone()
            if row is None:
                return None
            run = conn.execute("SELECT mode FROM runs WHERE run_id = ?", (row["run_id"],)).fetchone()
            rec = row_to_record(row)
            return {
                **summarize(rec, key, run["mode"] if run else "unknown"),
                "invalid_detail": rec.invalid_detail,
                "resource_pool": rec.resource_pool,
                "final_allocation": rec.final_allocation,
                # Evaluator-only data (hidden from agents during play).
                "valuation_A": rec.valuation_A, "valuation_B": rec.valuation_B,
                "usage": {k: row[k] for k in ("api_calls", "api_attempts", "input_tokens",
                                              "output_tokens", "latency_s", "cost_usd")},
                "events": transcript_events(row, rec),
            }
        finally:
            conn.close()


def current_provenance() -> dict:
    return {"protocol_id": provenance.PROTOCOL_ID, "spec_version": provenance.spec_version(),
            "prompt_hash": provenance.prompt_hash(), "code_version": provenance.code_version()}


def setup() -> dict:
    """The approved configuration, read from the modules that enforce it."""
    return {
        "conditions": list(CONDITIONS),
        "system_instructions": SYSTEM_INSTRUCTIONS,
        "structured_block": STRUCTURED_V1_BLOCK,
        "scientific": APPROVED_SCIENTIFIC,
        "claude_thinking": APPROVED_CLAUDE_THINKING,
        "operational": APPROVED_OPERATIONAL,
        "max_rounds": APPROVED_MAX_ROUNDS,
        "max_transport_reruns": APPROVED_MAX_TRANSPORT_RERUNS,
        "prices": APPROVED_PRICES,
        "environment": provenance.environment_params(3),
        "pilot": {"seeds": list(PILOT_SEEDS), "total": PILOT_TOTAL,
                  "cells": [asdict(c) for c in PILOT_CELLS]},
        "full": {"seeds": list(FULL_RUN_SEEDS), "instances": FULL_RUN_INSTANCES,
                 "total": FULL_RUN_TOTAL, "source": "docs/spec.md §8.3 (no full-run driver in code yet)"},
    }

