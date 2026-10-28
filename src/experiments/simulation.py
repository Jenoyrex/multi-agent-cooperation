"""Offline simulation pilot. SIMULATED DATA ONLY.

Runs the frozen pilot matrix (8 cells x seeds 30000-30002 = 24 negotiations,
the same condition labels, seat swaps and first-mover swaps) with two
deterministic MockAgents instead of Claude and GPT, under the network
tripwire, at zero cost. It validates the local pipeline: negotiation, outcome
classification, persistence, independent re-checks of every record, metrics
and report generation.

It says nothing about how any LLM negotiates. MockAgents never read
instructions, so the condition label cannot change their behaviour: every
paired baseline/structured difference is 0 by construction, and the analysis
checks that it is. Every run is stored with mode='simulation', every agent is
named SIMULATED-mock-*, and the analysis refuses any database that holds
anything else.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from pathlib import Path
from statistics import mean
from typing import List

from src.agents.mock_agent import MockAgent
from src.environment.allocation import compute_utility
from src.environment.optimum import compute_optimal_welfare
from src.environment.resources import generate_resource_pool
from src.environment.valuations import generate_valuation
from src.evaluation import metrics as M
from src.experiments.config import APPROVED_MAX_ROUNDS, ExperimentConfig
from src.experiments.pilot import PILOT_CELLS, PILOT_SEEDS, pilot_matrix
from src.experiments.runner import run_batch
from src.negotiation.protocol import NegotiationRecord

MODE = "simulation"
AGENT_PREFIX = "SIMULATED-mock-"
LABEL = ("SIMULATED DATA: deterministic mock agents on synthetic instances. "
         "Not evidence about Claude, GPT or any LLM.")
# Two fixed rule-based personas. X sits where the real design seats Claude,
# Y where it seats GPT: only the seat-swap structure is reused.
PERSONAS = {
    "X": dict(opening_ask=0.75, min_ask=0.35, concession_step=0.08, accept_threshold=0.55),
    "Y": dict(opening_ask=0.65, min_ask=0.30, concession_step=0.07, accept_threshold=0.35),
}
METRICS = ("agreed", "rwe", "ew")  # H3, H1, H2: defined for every negotiation (failures score 0)


def sim_agents(cell) -> tuple:
    x, y = (MockAgent(name=f"{AGENT_PREFIX}{p}", **PERSONAS[p]) for p in "XY")
    return (x, y) if cell.claude_seat == "A" else (y, x)


def sim_config(cell) -> ExperimentConfig:
    return ExperimentConfig(mode=MODE, method=cell.condition, model_A_label="mock", model_B_label="mock",
                            num_negotiations=len(PILOT_SEEDS), base_seed=PILOT_SEEDS[0],
                            max_rounds=APPROVED_MAX_ROUNDS, num_categories=3,
                            first_mover_policy=cell.first_mover)


async def run_simulation(db_path) -> List[NegotiationRecord]:
    """The 24 simulated negotiations, as 8 batches (one per cell), stored in db_path."""
    records = []
    for cell in PILOT_CELLS:
        records += await run_batch(sim_config(cell), *sim_agents(cell), db_path)
    return records


# ------------------------------------------------------------------ reading
def load_rows(db_path) -> list[dict]:
    """Every stored negotiation with its run's mode. Refuses a database that
    holds anything but simulated mock-agent data."""
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT n.*, r.mode, r.first_mover_policy AS run_policy FROM negotiations n JOIN runs r USING (run_id) "
            "ORDER BY n.method, n.model_A, n.first_mover, n.instance_seed")]
        modes = {m for (m,) in conn.execute("SELECT DISTINCT mode FROM runs")}
    finally:
        conn.close()
    foreign = sorted(modes - {MODE}) + sorted({r[k] for r in rows for k in ("model_A", "model_B")
                                               if not r[k].startswith(AGENT_PREFIX)})
    if foreign:
        raise ValueError(f"{db_path} is not simulated mock-agent data (found {foreign}); refusing to analyse it "
                         "as a simulation.")
    return rows


def to_record(row: dict) -> NegotiationRecord:
    return NegotiationRecord(
        instance_seed=row["instance_seed"], method=row["method"], model_A=row["model_A"], model_B=row["model_B"],
        first_mover=row["first_mover"], outcome=row["outcome"], num_rounds=row["num_rounds"],
        final_allocation=json.loads(row["final_allocation_json"]), utility_A=row["utility_A"],
        utility_B=row["utility_B"], total_welfare=row["total_welfare"], optimal_welfare=row["optimal_welfare"],
        invalid_reason=row["invalid_reason"])


def cell_of(row: dict) -> int:
    seat = "A" if row["model_A"] == f"{AGENT_PREFIX}X" else "B"
    return next(c.index for c in PILOT_CELLS
                if (c.condition, c.claude_seat, c.first_mover) == (row["method"], seat, row["first_mover"]))


# --------------------------------------------------------------- re-checks
def verify(rows: list[dict]) -> list[str]:
    """Independent re-checks of the stored records; [] if all pass. Regenerates
    each instance from its seed and recomputes the optimum and utilities instead
    of trusting the stored values."""
    problems = []
    pairs = sorted((cell_of(r), r["instance_seed"]) for r in rows)
    expected = sorted((c.index, s) for c, s in pilot_matrix())
    if pairs != expected:
        problems.append(f"matrix: stored (cell, seed) pairs differ from the 24 planned ({len(pairs)} stored)")
    for r in rows:
        where = f"cell {cell_of(r)} seed {r['instance_seed']}"
        pool = generate_resource_pool(seed=r["instance_seed"], num_categories=3)
        val = {role: generate_valuation(r["instance_seed"], role, pool) for role in "AB"}
        if json.loads(r["resource_pool_json"]) != pool or \
                {role: json.loads(r[f"valuation_{role}_json"]) for role in "AB"} != val:
            problems.append(f"{where}: stored instance differs from its seed")
        _, optimum = compute_optimal_welfare(pool, val["A"], val["B"])
        if abs(optimum - r["optimal_welfare"]) > 1e-9:
            problems.append(f"{where}: optimal welfare {r['optimal_welfare']} != recomputed {optimum}")
        alloc = json.loads(r["final_allocation_json"])
        utils = {role: compute_utility(alloc[role], val[role]) if alloc else 0.0 for role in "AB"}
        if any(abs(utils[role] - r[f"utility_{role}"]) > 1e-9 for role in "AB"):
            problems.append(f"{where}: utilities differ from the recomputed ones")
        if alloc and any(alloc["A"][c] + alloc["B"][c] != q for c, q in pool.items()):
            problems.append(f"{where}: allocation does not split the pool exactly")
        if r["first_mover"] != r["run_policy"] or r["api_calls"] != 0:
            problems.append(f"{where}: first mover differs from the cell's, or an API call was recorded")
    return problems


# ----------------------------------------------------------------- analysis
def analyse(rows: list[dict]) -> dict:
    """Descriptive only (no inferential test). Unit of analysis: the instance
    (seed); a condition's per-instance value is the mean over its 4 cells."""
    per_neg = []
    for r in rows:
        rec = to_record(r)
        per_neg.append({"cell": cell_of(r), "condition": r["method"], "seed": r["instance_seed"],
                        "model_A": r["model_A"], "model_B": r["model_B"], "first_mover": r["first_mover"],
                        "outcome": r["outcome"], "invalid_reason": r["invalid_reason"] or "",
                        "rounds": r["num_rounds"], "utility_A": round(r["utility_A"], 6),
                        "utility_B": round(r["utility_B"], 6), "optimal_welfare": round(r["optimal_welfare"], 6),
                        "agreed": int(rec.outcome == "agreed"), "rwe": round(M.relative_welfare_efficiency(rec), 6),
                        "ew": round(M.egalitarian_welfare(rec), 6),
                        "equitability": None if M.equitability(rec) is None else round(M.equitability(rec), 6)})
    per_neg.sort(key=lambda n: (n["cell"], n["seed"]))
    by_condition = {}
    for cond in sorted({n["condition"] for n in per_neg}):
        ns = [n for n in per_neg if n["condition"] == cond]
        agreed_eq = [n["equitability"] for n in ns if n["equitability"] is not None]
        by_condition[cond] = {
            "negotiations": len(ns), "outcomes": {o: sum(n["outcome"] == o for n in ns)
                                                  for o in ("agreed", "walked_away", "timeout", "invalid_action")},
            **{f"mean_{m}": round(mean(n[m] for n in ns), 6) for m in METRICS},
            "mean_equitability_agreed_only": round(mean(agreed_eq), 6) if agreed_eq else None,
            "agreed_n_for_equitability": len(agreed_eq), "mean_rounds": round(mean(n["rounds"] for n in ns), 6)}
    paired = {}
    for m in METRICS:
        diffs = [mean(n[m] for n in per_neg if n["seed"] == s and n["condition"] == "structured_v1")
                 - mean(n[m] for n in per_neg if n["seed"] == s and n["condition"] == "baseline_v1")
                 for s in PILOT_SEEDS]
        paired[m] = {"per_instance_structured_minus_baseline": [round(d, 6) for d in diffs],
                     "mean": round(mean(diffs), 6)}
    return {"data_label": LABEL, "instances": len(PILOT_SEEDS), "negotiations": len(per_neg),
            "per_negotiation": per_neg, "by_condition": by_condition, "paired_by_instance": paired,
            "condition_has_no_effect_by_construction": all(v["mean"] == 0 and not any(v["per_instance_structured_minus_baseline"])
                                                          for v in paired.values())}


# ---------------------------------------------------------------- artifacts
def _csv(per_neg: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["data_label", *per_neg[0]], lineterminator="\n")
    writer.writeheader()
    for n in per_neg:
        writer.writerow({"data_label": "SIMULATED", **{k: "" if v is None else v for k, v in n.items()}})
    return buf.getvalue()


def write_artifacts(out_dir: Path, summary: dict, problems: list[str], blocked: list) -> dict:
    """negotiations.csv, summary.json and REPORT.md: byte-identical on every
    run (no timestamps, run ids or paths). Returns {name: sha256}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"negotiations.csv": _csv(summary["per_negotiation"]),
             "summary.json": json.dumps({**summary, "integrity_problems": problems, "network_attempts_blocked":
                                         len(blocked)}, indent=2, sort_keys=True) + "\n"}
    hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}
    files["REPORT.md"] = report(summary, problems, blocked, hashes)
    for name, text in files.items():
        (out_dir / name).write_text(text, encoding="utf-8", newline="\n")
    return {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}


def report(summary: dict, problems: list[str], blocked: list, hashes: dict) -> str:
    bc, pd = summary["by_condition"], summary["paired_by_instance"]
    rows = "\n".join(
        f"| {c} | {v['negotiations']} | {v['outcomes']['agreed']} / {v['outcomes']['walked_away']} / "
        f"{v['outcomes']['timeout']} / {v['outcomes']['invalid_action']} | {v['mean_agreed']:.3f} | "
        f"{v['mean_rwe']:.3f} | {v['mean_ew']:.3f} | "
        f"{'n/a' if v['mean_equitability_agreed_only'] is None else format(v['mean_equitability_agreed_only'], '.3f')}"
        f" (n={v['agreed_n_for_equitability']}) | {v['mean_rounds']:.2f} |" for c, v in bc.items())
    paired = "\n".join(f"| {m} | {', '.join(f'{d:+.3f}' for d in v['per_instance_structured_minus_baseline'])} | "
                       f"{v['mean']:+.3f} |" for m, v in pd.items())
    return f"""# Offline simulation pilot: SIMULATED DATA, not an LLM experiment

> **{LABEL}**
> No empirical experiment with a real LLM has been conducted. The research question (does the
> structured negotiation instruction strategy improve cooperation relative to baseline prompting?)
> remains **empirically unanswered**. Nothing below may be cited as a finding about Claude, GPT or
> any other model.

Generated by `PYTHONPATH=. py scripts/run_offline_simulation.py`. Identical bytes on every run.

## 1. What was run
- Matrix: the frozen pilot design, 8 cells (condition label x mock-X seat x first mover) x instance
  seeds {", ".join(map(str, PILOT_SEEDS))} = {summary['negotiations']} negotiations, max_rounds {APPROVED_MAX_ROUNDS}, K = 3.
- Agents: two deterministic rule-based `MockAgent`s, `{AGENT_PREFIX}X` and `{AGENT_PREFIX}Y`
  (parameters in `src/experiments/simulation.py`). X sits where the real design seats Claude,
  Y where it seats GPT. **They never read instructions**, so the condition labels
  `baseline_v1` / `structured_v1` are matrix labels only and cannot change behaviour.
- Storage: runs stored with `mode='simulation'`, outside `results/` (the research dashboard does not read it).
- Cost: $0. Mock agents make no API calls, and the zero-cost guard refuses to build any real provider
  client. Network attempts blocked by the tripwire: {len(blocked)}.

## 2. Software validation (what this run checks)
- Independent re-checks of every stored record: (cell, seed) matrix complete and unique; instance
  regenerated from its seed; optimum recomputed; utilities recomputed from the stored allocation;
  allocations split the pool exactly; first mover matches the cell; zero API calls.
  Result: **{"all passed" if not problems else f"{len(problems)} problem(s): " + "; ".join(problems)}**.
- Expected-by-construction check: every paired structured - baseline difference is exactly 0:
  **{"confirmed" if summary["condition_has_no_effect_by_construction"] else "VIOLATED: pipeline defect"}**.
- The automated test suite (`PYTHONPATH=. py -m pytest -q`) covers the protocol, outcome semantics,
  metrics, storage constraints, budget ledger and resume, and the zero-cost guard; counts are recorded in `PROGRESS.md`.

## 3. Simulation results (descriptive; mock agents only)
Outcomes are agreed / walked_away / timeout / invalid_action. RWE (H1) and EW (H2) score failures as 0. Agreement rate is H3.
Equitability is agreement-only.

| condition label | n | outcomes | agreement | mean RWE | mean EW | mean equitability | mean rounds |
|---|---|---|---|---|---|---|---|
{rows}

Paired by instance (unit of analysis = instance; each value is a mean over that instance's 4 cells):

| metric | structured - baseline, per seed | mean |
|---|---|---|
{paired}

These numbers describe two fixed rules on three synthetic instances. No inferential test was run:
the preregistered analysis (spec §8.9) is not implemented, and n = 3 simulated instances could not support one.

## 4. Empirical LLM results
**None.** No real-model pilot and no full experiment were run, and no provider API was called.
The hypotheses H1-H3 have not been tested.

## 5. Limitations
- Mock agents are deterministic heuristics that ignore instructions; the simulation shows that the pipeline runs and records correctly,
  not how the conditions compare.
- Outcome paths that these two personas do not produce here are covered by the unit tests, not by this run.
- The 24 negotiations come from only 3 instances.

## 6. Artifact fingerprints (sha256)
- negotiations.csv: `{hashes['negotiations.csv']}`
- summary.json: `{hashes['summary.json']}`
"""
