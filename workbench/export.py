"""Exports of one simulation, built from its stored document. Every file carries the
simulation's data label; nothing is recomputed from anything but the stored records."""
from __future__ import annotations

import csv
import io
import json

from sandbox import analysis as SA
from workbench import readable
from workbench.analytics import analyse

FILES = {
    "config.json": "Configuration with agent and environment snapshots, design and provenance",
    "environment.json": "The environment definition, re-importable in the builder",
    "transcripts.json": "Every negotiation's turn-by-turn events",
    "negotiations.csv": "One row per negotiation",
    "summary.csv": "Aggregate metrics per condition and per agent",
    "analytics.json": "Behavioural analytics with evidence",
    "behaviour.csv": "Behaviour profile per agent and group: mean, 95% interval and n",
    "metrics.csv": "Each behaviour metric per negotiation and agent, the numbers the charts are computed from",
    "usage.csv": "Calls, tokens and cost per negotiation",
    "report.md": "Reproducibility and limitations report",
}
ROW = ("index", "repetition", "seed", "condition", "seat_A", "seat_B", "first_mover", "status", "outcome",
       "invalid_reason", "rounds", "utility_A", "utility_B", "optimal_welfare", "rwe", "ew", "swe", "equitability", "envy_free")
EXTRA = ("resource_set", "persona", "pre_event_optimal_welfare", "events_fired", "api_calls", "input_tokens", "output_tokens", "cost")


def _csv(header, rows) -> str:
    buf = io.StringIO()
    # RFC 4180 row endings: with "\r\n" as the terminator every Python version quotes a field holding \r or \n
    # (Python 3.10 did not quote a bare \r with "\n", so such a cell could split a row in a spreadsheet).
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    # A leading = + - @ (or tab / carriage return, per OWASP) would make a spreadsheet evaluate a name as a formula.
    w.writerows([["" if v is None else f"'{v}" if isinstance(v, str) and v and v[0] in "=+-@\t\r" else v for v in r] for r in rows])
    return buf.getvalue()


def _set_name(cfg: dict, t: dict) -> str:
    return (cfg.get("environments") or [cfg["environment"]])[t.get("env_index", 0)]["name"]


BEHAVIOUR_HEADER = ("data_label", "data_source", "group", "agent", "agent_name", "metric", "mean", "ci_low", "ci_high", "n_repetitions", "n_negotiations", "small_n")


def behaviour_rows(doc: dict) -> list[list]:
    cfg, a = doc["config"], analyse(doc)["sandbox"]
    sources, label = cfg.get("source_labels") or {}, cfg["label"].split(":")[0]
    def rows(group, profiles):
        return [[label, sources.get(k, ""), group, k, cfg["agents"][k]["name"], m, s["mean"], *(s["ci"] or (None, None)), s["n"], s["n_negotiations"], s["small_n"]]
                for k, p in profiles.items() for m, s in p.items()]
    out = rows("all", a["profiles"])
    for g in (a["sweep"] or {}).get("groups", []):
        out += rows(g["name"], g["agents"])
    return out


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
        body = _csv(("data_label",) + ROW + EXTRA, [[label.split(":")[0], t["index"], t["repetition"], t["seed"], t["condition"],
                                             t["seats"]["A"], t["seats"]["B"], t["first_mover"], t["status"]]
                                            + [t.get(k) for k in ROW[8:]]
                                            + [_set_name(cfg, t), t.get("persona"), t.get("pre_event_optimal_welfare"), len(t.get("events_fired") or []),
                                               *[(t.get("usage") or {}).get(k) for k in ("api_calls", "input_tokens", "output_tokens", "cost_usd")]]
                                            for t in doc["trials"]])
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
    elif name == "behaviour.csv":
        body = _csv(BEHAVIOUR_HEADER, behaviour_rows(doc))
    elif name == "metrics.csv":
        body = _csv(SA.METRIC_ROW_HEADER, SA.metric_rows(doc))
    elif name == "usage.csv":
        u = SA.usage(doc)
        body = _csv(("data_label", "negotiation", "status", "api_calls", "input_tokens", "output_tokens", "cost"),
                    [[label.split(":")[0], r["index"], r["status"], r["api_calls"], r["input_tokens"], r["output_tokens"], r["cost_usd"]] for r in u["per_negotiation"]])
    else:
        body = report(doc)
    return {"filename": f"{stem}-{name}", "content_type": "text/csv" if name.endswith(".csv") else
            "text/markdown" if name.endswith(".md") else "application/json", "content": body}


def inference_md(inf: dict) -> str:
    if not inf.get("available"):
        return f"Not run: {inf.get('reason')}"
    lines = [f"> {inf['scope']}", ""] + [f"- {h}: {text}" for h, text in inf["interpretations"].items()]
    return "\n".join(lines)


def _cell(text) -> str:
    """User text inside a Markdown table cell: no column breaks, no line breaks."""
    return str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def report(doc: dict) -> str:
    """The Markdown report. User text (names, messages) cannot add HTML: every "<" is written as "&lt;"."""
    return _report(doc).replace("<", "&lt;")


def _report(doc: dict) -> str:
    cfg, a, p = doc["config"], analyse(doc), doc["provenance"]
    env = cfg["environment"]
    agents = "\n".join(f"- {k}: **{d['name']}** ({d['kind']}), parameters `{json.dumps(d['params'])}`" for k, d in cfg["agents"].items())
    conds = "\n".join(f"| {c} | {v['n']} | {_fmt(v['agreement_rate'])} | {_fmt(v['rwe'])} | {_fmt(v['ew'])} | "
                      f"{_fmt(v['swe'])} (n={v['n_agreed']}) | {_fmt(v['equitability'])} | {_fmt(v['mean_rounds'], 2)} |"
                      for c, v in a["by_condition"].items())
    obs = "\n".join(f"- [{o['basis']}] {o['text']}" for o in a["observations"]) or "- None (no completed negotiations)."
    sb = a["sandbox"]
    real = cfg["data_kind"] in ("local-model", "api-model")
    scope = ("The numbers below come from the model(s) named here, on these resources, in these runs. They are exploratory and are not the research study's results."
             if real else "No empirical experiment with a real LLM was run here. These numbers describe the agents listed below on this\n> environment, and nothing else.")
    sources = "\n".join(f"- Data source for {k}: {v}" for k, v in (cfg.get("source_labels") or {}).items())
    u = sb["usage"]
    usage = f"{u['calls']} model call(s), {u['attempts']} attempt(s), {_fmt(u['tokens'])} tokens, cost {_fmt(u['cost'], 4) if u['priced'] else 'not priced (no prices entered)'}." if u["calls"] else "No model calls."
    warn = "\n".join(f"- {w}" for w in sb["warnings"]) or "- None."
    finds = "\n".join(f"- {f['text']}" for f in sb["findings"]) or "- None (no completed negotiations)."
    def line(name, s, unit):
        if s["mean"] is None:
            return f"| {name} | not observed | | 0 |"
        ci = f"{s['ci'][0]:.3f} to {s['ci'][1]:.3f}" if s["ci"] else "too few"
        return f"| {name} | {s['mean']:.3f} | {ci} | {s['n']} |"
    profiles = "\n\n".join(f"**{cfg['agents'][k]['name']} ({k})**\n\n| measure | mean | 95% CI | n (repetitions) |\n|---|---|---|---|\n" +
                           "\n".join(line(sb["metrics"][m]["label"], s, sb["metrics"][m]["unit"]) for m, s in p.items()) for k, p in sb["profiles"].items()) or "None."
    defs = "\n".join(f"- **{v['label']}**: {v['definition']}" for v in sb["metrics"].values())
    envs = cfg.get("environments") or [env]
    sweep_md = ""
    if sb["sweep"]:
        sweep_md = "\n## Comparison between groups\n" + sb["sweep"]["note"] + "\n\n| group | n | deals reached | difference | 95% CI |\n|---|---|---|---|---|\n" + "\n".join(
            f"| {_cell(g['name'])} | {g['outcomes']['agreement']['n']} | {_fmt(g['outcomes']['agreement']['mean'])} | "
            f"{_fmt(g['differences'].get('agreement', {}).get('mean'))} | {' to '.join(f'{x:.3f}' for x in (g['differences'].get('agreement', {}).get('ci') or [])) or 'n/a'} |"
            for g in sb["sweep"]["groups"]) + "\n"
    react_md = ""
    if sb["reactions"]:
        r = sb["reactions"]
        react_md = (f"\n## Reaction to resource events\nProtocol `sandbox_v1`. {r['n_reached']} of {r['n_negotiations']} negotiations reached the event. "
                    f"Mean best possible total (W*): {_fmt(r['pre_event_welfare']['mean'], 1)} before, {_fmt(r['final_welfare']['mean'], 1)} after.\n\n"
                    "| agent | demand before | demand after | change | 95% CI | n |\n|---|---|---|---|---|---|\n" + "\n".join(
                        f"| {_cell(cfg['agents'][k]['name'])} | {_fmt(v['before']['mean'])} | {_fmt(v['after']['mean'])} | {_fmt(v['shift']['mean'])} | "
                        f"{' to '.join(f'{x:.3f}' for x in (v['shift']['ci'] or [])) or 'too few'} | {v['shift']['n']} |" for k, v in r["agents"].items()) + "\n")
    R = readable.summary(doc, a)
    agent_md = []
    for g in R["agents"]:
        agent_md += [f"**{g['name']}**" + (f" ({g['source']})" if g["source"] else ""), "", *(f"- {line}" for line in g["lines"]), ""]
    plain = "\n".join([
        "## The short version", R["headline"], "",
        *(f"- {pt['text']}" for pt in R["points"]), "",
        "## Key numbers", "| measure | value | what it means | exact |", "|---|---|---|---|",
        *(f"| {_cell(k['label'])} | **{_cell(k['value'])}** | {_cell(k['meaning'])} | {_cell(k['exact'])} |" for k in R["numbers"]), "",
        "## Each agent, in plain words", "", *agent_md,
        "## Good to know before you quote these numbers", *(f"- {c}" for c in R["caveats"]), "",
        "The full details follow: every table, definition, limitation and record behind the summary above.", ""])
    words = "\n".join(f"- **{t}**: {d}" for t, d in R["glossary"])
    return f"""# {cfg['name']}

> **{cfg['label']}**
> {scope}
> {sb['exploratory']}

{plain}
## Configuration
- Simulation `{doc['id']}`, status **{doc['status']}**, {a['n_completed']} of {doc['plan_size']} negotiations completed
  {f"({', '.join(f'{v} {k}' for k, v in a['not_outcomes'].items())}, not outcomes)" if a['not_outcomes'] else ''}.
- Resource set(s): {'; '.join(f"**{e['name']}** ({e['source']}, {e['max_rounds']} rounds, seat {e['first_mover']} opens by default)" for e in envs)}.
- Design: {cfg['repetitions']} repetition(s) from seed {cfg['base_seed']}, conditions {', '.join(cfg['conditions'])},
  seats swapped: {cfg['swap_seats']}, opening seat swapped: {cfg['swap_first_mover']}.
{agents}
{sources}
- Model usage: {usage}

## Read this first
{warn}

## Findings
{finds}

## Behaviour profiles
Averaged per repetition first, so n counts repetitions. Intervals are 95% bootstrap intervals over those averages.

{profiles}
{sweep_md}{react_md}
## What the measures mean
{defs}

## Results by condition (descriptive)
| condition | n | agreement | mean RWE | mean EW | mean SWE (agreed) | equitability (agreed) | mean rounds |
|---|---|---|---|---|---|---|---|
{conds}

RWE and EW score failed negotiations as 0; SWE and equitability use agreed negotiations only.

## Exploratory inference
{inference_md(a['inference'])}

## Observations
{obs}

## Reproducibility
- Protocol `{p['protocol_id']}`, spec `{p['spec_version']}`, prompt hash `{p['prompt_hash']}`, code `{p['code_version']}`.
- Engine: {p['engine']}. Zero-cost mode: {p['zero_cost_mode']}.
- Re-running the same configuration reproduces deterministic agents exactly; models, HTTP agents and Python files may differ.

## Limitations
{chr(10).join(f'- {x}' for x in a['not_available'])}

## Words used in this report
{words}
- Two agents per negotiation: the frozen protocol is bilateral.
"""
