"""Exports of one simulation, built from its stored document. Every file carries the
simulation's data label; nothing is recomputed from anything but the stored records."""
from __future__ import annotations

import csv
import io
import json

from workbench.analytics import analyse

FILES = {
    "config.json": "Configuration with agent and environment snapshots, design and provenance",
    "environment.json": "The environment definition, re-importable in the builder",
    "transcripts.json": "Every negotiation's turn-by-turn events",
    "negotiations.csv": "One row per negotiation",
    "summary.csv": "Aggregate metrics per condition and per agent",
    "analytics.json": "Behavioural analytics with evidence",
    "report.md": "Reproducibility and limitations report",
}
ROW = ("index", "repetition", "seed", "condition", "seat_A", "seat_B", "first_mover", "status", "outcome",
       "invalid_reason", "rounds", "utility_A", "utility_B", "optimal_welfare", "rwe", "ew", "swe", "equitability", "envy_free")


def _csv(header, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    # A leading = + - @ would make a spreadsheet evaluate a name as a formula.
    w.writerows([["" if v is None else f"'{v}" if isinstance(v, str) and v[:1] in "=+-@" else v for v in r] for r in rows])
    return buf.getvalue()


def _fmt(v, p=3):
    return "n/a" if v is None else f"{v:.{p}f}" if isinstance(v, float) else str(v)


def build(doc: dict, name: str) -> dict:
    """{filename, content_type, content} for one export, or KeyError."""
    if name not in FILES:
        raise KeyError(name)
    cfg, label = doc["config"], doc["config"]["label"]
    stem = f"simulation-{doc['id']}"
    if name == "config.json":
        body = json.dumps({"data_label": label, "id": doc["id"], "status": doc["status"], "created": doc["created"],
                           "config": cfg, "provenance": doc["provenance"]}, indent=2)
    elif name == "environment.json":
        env = {k: v for k, v in cfg["environment"].items() if k not in ("id", "builtin", "created", "updated")}
        body = json.dumps(env, indent=2)
    elif name == "transcripts.json":
        body = json.dumps({"data_label": label, "negotiations": [
            {k: t.get(k) for k in ("index", "seed", "condition", "seats", "first_mover", "status", "outcome", "events", "pool", "points")}
            for t in doc["trials"]]}, indent=2)
    elif name == "negotiations.csv":
        body = _csv(("data_label",) + ROW, [[label.split(":")[0], t["index"], t["repetition"], t["seed"], t["condition"],
                                             t["seats"]["A"], t["seats"]["B"], t["first_mover"], t["status"]]
                                            + [t.get(k) for k in ROW[8:]] for t in doc["trials"]])
    elif name == "summary.csv":
        a = analyse(doc)
        rows = [[label.split(":")[0], "condition", c, v["n"], v["n_agreed"], v["agreement_rate"], v["rwe"], v["ew"], v["swe"],
                 v["equitability"], v["invalid_rate"], v["mean_rounds"], None] for c, v in a["by_condition"].items()]
        rows += [[label.split(":")[0], "agent", f"{k} {cfg['agents'][k]['name']}", p["negotiations"], None, None, None, None, None,
                  None, None, None, p["utility_mean"]] for k, p in a["agents"].items()]
        body = _csv(("data_label", "group", "name", "n", "n_agreed", "agreement_rate", "mean_rwe", "mean_ew", "mean_swe_agreed",
                     "mean_equitability_agreed", "invalid_rate", "mean_rounds", "mean_utility"), rows)
    elif name == "analytics.json":
        body = json.dumps(analyse(doc), indent=2)
    else:
        body = report(doc)
    return {"filename": f"{stem}-{name}", "content_type": "text/csv" if name.endswith(".csv") else
            "text/markdown" if name.endswith(".md") else "application/json", "content": body}


def report(doc: dict) -> str:
    cfg, a, p = doc["config"], analyse(doc), doc["provenance"]
    env = cfg["environment"]
    agents = "\n".join(f"- {k}: **{d['name']}** ({d['kind']}), parameters `{json.dumps(d['params'])}`" for k, d in cfg["agents"].items())
    conds = "\n".join(f"| {c} | {v['n']} | {_fmt(v['agreement_rate'])} | {_fmt(v['rwe'])} | {_fmt(v['ew'])} | "
                      f"{_fmt(v['swe'])} (n={v['n_agreed']}) | {_fmt(v['equitability'])} | {_fmt(v['mean_rounds'], 2)} |"
                      for c, v in a["by_condition"].items())
    obs = "\n".join(f"- [{o['basis']}] {o['text']}" for o in a["observations"]) or "- None (no completed negotiations)."
    return f"""# {cfg['name']}

> **{cfg['label']}**
> No empirical experiment with a real LLM was run here. These numbers describe the agents listed below on this
> environment, and nothing else.

## Configuration
- Simulation `{doc['id']}`, status **{doc['status']}**, {a['n_completed']} of {doc['plan_size']} negotiations completed
  {f"({', '.join(f'{v} {k}' for k, v in a['not_outcomes'].items())}, not outcomes)" if a['not_outcomes'] else ''}.
- Environment: **{env['name']}** ({env['source']}), {env['max_rounds']} rounds, seat {env['first_mover']} opens by default.
- Design: {cfg['repetitions']} repetition(s) from seed {cfg['base_seed']}, conditions {', '.join(cfg['conditions'])},
  seats swapped: {cfg['swap_seats']}, opening seat swapped: {cfg['swap_first_mover']}.
{agents}

## Results by condition (descriptive)
| condition | n | agreement | mean RWE | mean EW | mean SWE (agreed) | equitability (agreed) | mean rounds |
|---|---|---|---|---|---|---|---|
{conds}

RWE and EW score failed negotiations as 0; SWE and equitability use agreed negotiations only.

## Observations
{obs}

## Reproducibility
- Protocol `{p['protocol_id']}`, spec `{p['spec_version']}`, prompt hash `{p['prompt_hash']}`, code `{p['code_version']}`.
- Engine: {p['engine']}. Zero-cost mode: {p['zero_cost_mode']}.
- Re-running the same configuration reproduces deterministic agents exactly; local HTTP agents may differ.

## Limitations
{chr(10).join(f'- {x}' for x in a['not_available'])}
- Two agents per negotiation: the frozen protocol is bilateral.
"""
