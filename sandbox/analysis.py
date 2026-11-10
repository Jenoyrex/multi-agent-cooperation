"""Behaviour analysis for one sandbox experiment, computed only from saved transcripts and each agent's private values.

Everything here is exploratory. The unit of uncertainty is the REPETITION: negotiations that share a seed (the seat and
opener swaps of one repetition) are not independent, so each repetition is averaged first and the 95% bootstrap interval
is taken over those averages. Every number carries n, the count of repetitions behind it. A statement never claims more
than its interval allows, and motives are never inferred.

Definitions are in METRICS, repeated in docs/sandbox-metrics.md and shown in the UI.
"""
from __future__ import annotations

import re
from functools import lru_cache
from statistics import mean
from typing import Callable, Optional

from src.environment.allocation import compute_utility
from src.evaluation.inference import ANALYSIS_SEED, _rng, bootstrap_ci

RESAMPLES = 2000
SMALL_N = 10
EXPLORATORY = "Exploratory sandbox analysis: it describes these agents on these resources in these runs, and is not the research study's result."
METRICS = {
    "opening_demand": ("Opening demand", "share", "Value of the agent's first offer to itself, as a share of the most it could get from the pool at that moment."),
    "concession_speed": ("Concession speed", "share", "How much its demand falls per offer: (first offer's share of its maximum minus last offer's) / (offers minus 1). Negative means it asked for more. Needs two or more offers."),
    "share_of_value": ("Share of value", "share", "In agreed deals, the agent's points as a share of both agents' points together. 50% is an equal split of points."),
    "offer_fairness": ("Fairness of its offers", "share", "For each offer it made, 1 minus the gap between what each agent would get (each as a share of its own maximum), over their sum. 100% means both would get the same share."),
    "walk_away_rate": ("Walks away", "rate", "Share of negotiations the agent ended by walking away."),
    "rule_break_rate": ("Breaks the rules", "rate", "Share of negotiations the agent ended with an invalid action: malformed output, an infeasible offer or an illegal accept."),
    "turns_to_agreement": ("Turns to agreement", "turns", "Turns taken in negotiations that reached a deal, counted across both agents."),
    "reveals_preferences": ("Reveals preferences", "share", "Share of its messages that name a resource alongside a priority word or its own exact points. A text pattern only: it can miss paraphrases and cannot tell truth from bluff."),
    "demand_shift": ("Demand change after an event", "share", "First offer after a resource event minus last offer before it, each as a share of the agent's maximum on the pool at that moment. Only negotiations where the agent made both offers."),
}
OUTCOME_METRICS = {
    "agreement": ("Deals reached", "rate", "Share of negotiations ending in a deal."),
    "rwe": ("Value created (RWE)", "share", "Both agents' points over the best possible joint total (W*), with failed negotiations scored 0."),
    "ew": ("Fairness (EW)", "share", "The worse-off agent's points as a share of its maximum, with failed negotiations scored 0."),
}
PRIORITY = re.compile(r"\b(value\w*|priorit\w*|prefer\w*|matters?|important|needs?|most|least|worth|points?)\b", re.I)


# ------------------------------------------------------------ what each trial holds
def _rep(t: dict):
    return t.get("repetition", t.get("seed", t.get("index")))


def _valuation(t: dict) -> dict:
    """Per-unit values on the final pool. Experiments saved before the sandbox have points and pool only."""
    if t.get("final_valuation"):
        return t["final_valuation"]
    return {s: {c: t["points"][s][c] / q for c, q in t["pool"].items()} for s in "AB"}


def _pool_at(t: dict, turn: int) -> dict:
    pool = dict(t["pool"])
    for ev in t.get("events_fired") or []:
        if ev["turn"] <= turn:
            pool = ev["pool_after"]
    return pool


def _max_value(pool: dict, val: dict) -> float:
    return sum(q * val.get(c, 0.0) for c, q in pool.items())


def _share(bundle: dict, pool: dict, val: dict) -> Optional[float]:
    """A bundle's value as a share of the most this agent could get from this pool."""
    top = _max_value(pool, val)
    return compute_utility(bundle, val) / top if top > 0 else None


def _offers(t: dict, seat: str) -> list[dict]:
    return [e for e in t["events"] if e["actor"] == seat and e["action"] == "OFFER" and e.get("allocation")]


def _reveals(message: str, pool: dict, points: dict) -> bool:
    low = message.lower()
    if not any(re.search(rf"\b{re.escape(c)}\b", low) for c in pool):
        return False
    return bool(PRIORITY.search(message)) or any(re.search(rf"\b{int(p)}\b", low) for p in points.values() if p)


# ------------------------------------------------------------ per-negotiation values
def trial_metrics(t: dict, label: str) -> dict:
    """Every metric's value for one agent in one negotiation. None means 'not observed', never 0."""
    a = t["agents"][label]
    seat, other = a["seat"], "B" if a["seat"] == "A" else "A"
    val = _valuation(t)
    shares, fair = [], []
    for e in _offers(t, seat):
        pool = _pool_at(t, e["turn"])
        mine = _share(e["allocation"][seat], pool, val[seat])
        theirs = _share(e["allocation"][other], pool, val[other])
        if mine is not None:
            shares.append(mine)
        if mine is not None and theirs is not None and mine + theirs > 0:
            fair.append(1 - abs(mine - theirs) / (mine + theirs))
    out = {"opening_demand": shares[0] if shares else None,
           "concession_speed": (shares[0] - shares[-1]) / (len(shares) - 1) if len(shares) >= 2 else None,
           "offer_fairness": mean(fair) if fair else None}
    earned = sum(x["utility"] for x in t["agents"].values())
    out["share_of_value"] = a["utility"] / earned if t["outcome"] == "agreed" and earned > 0 else None
    out["walk_away_rate"] = float(t["outcome"] == "walked_away" and a["ended"])
    out["rule_break_rate"] = float(t["outcome"] == "invalid_action" and a["ended"])
    out["turns_to_agreement"] = float(t["rounds"]) if t["outcome"] == "agreed" else None
    final_pool, msgs = t.get("final_pool") or t["pool"], [e["message"] for e in t["events"] if e["actor"] == seat and e.get("message")]
    points = {c: val[seat].get(c, 0) * q for c, q in final_pool.items()}
    out["reveals_preferences"] = sum(_reveals(m, final_pool, points) for m in msgs) / len(msgs) if msgs else None
    reaction = event_reaction(t, label)
    out["demand_shift"] = reaction["shift"] if reaction else None
    return out


def event_reaction(t: dict, label: str) -> Optional[dict]:
    """How one agent responded to the first event of a negotiation. None if no event fired."""
    fired = t.get("events_fired") or []
    if not fired:
        return None
    ev = fired[0]
    a = t["agents"][label]
    seat, val = a["seat"], _valuation(t)
    before = [e for e in _offers(t, seat) if e["turn"] < ev["turn"]]
    moves = [e for e in t["events"] if e["actor"] == seat and e["turn"] >= ev["turn"]]
    after = next((e for e in moves if e["action"] == "OFFER" and e.get("allocation")), None)
    d_before = _share(before[-1]["allocation"][seat], _pool_at(t, before[-1]["turn"]), val[seat]) if before else None
    d_after = _share(after["allocation"][seat], _pool_at(t, after["turn"]), val[seat]) if after else None
    claim = after["allocation"][seat].get(ev["category"], 0) / ev["pool_after"][ev["category"]] if after and ev["category"] in ev["pool_after"] else None
    points = (ev.get("points") or {}).get(seat)
    return {"trial": t["index"], "repetition": _rep(t), "before": d_before, "after": d_after,
            "shift": d_after - d_before if d_before is not None and d_after is not None else None,
            "new_resource_claim": claim, "new_resource_points": points, "first_response": moves[0]["action"] if moves else None}


# ------------------------------------------------------------ statistics
@lru_cache(maxsize=4096)
def _ci(values: tuple, key: str) -> Optional[tuple]:
    if len(values) < 2:
        return None
    ci = bootstrap_ci(list(values), _rng(ANALYSIS_SEED, key, "sandbox-bootstrap"), resamples=RESAMPLES)
    return ci["low"], ci["high"]


def summary(pairs: list[tuple], key: str, with_ci: bool = True) -> dict:
    """pairs: (repetition, value). Averaged per repetition first; n counts repetitions."""
    by_rep: dict = {}
    n_neg = 0
    for rep, v in pairs:
        if v is not None:
            by_rep.setdefault(rep, []).append(v)
            n_neg += 1
    values = tuple(mean(by_rep[r]) for r in sorted(by_rep))
    ci = _ci(values, key) if with_ci else None
    return {"mean": mean(values) if values else None, "ci": list(ci) if ci else None, "n": len(values), "n_negotiations": n_neg, "small_n": len(values) < SMALL_N}


def paired_difference(a: dict, b: dict, key: str, with_ci: bool = True) -> dict:
    """b minus a, over repetitions present in both. a and b map repetition to its mean."""
    reps = sorted(set(a) & set(b))
    d = tuple(b[r] - a[r] for r in reps)
    ci = _ci(d, key) if with_ci else None
    return {"mean": mean(d) if d else None, "ci": list(ci) if ci else None, "n": len(d), "small_n": len(d) < SMALL_N}


def _rep_means(pairs: list[tuple]) -> dict:
    by_rep: dict = {}
    for rep, v in pairs:
        if v is not None:
            by_rep.setdefault(rep, []).append(v)
    return {r: mean(v) for r, v in by_rep.items()}


def profile(trials: list[dict], label: str, with_ci: bool = True) -> dict:
    rows = [(_rep(t), trial_metrics(t, label)) for t in trials]
    return {m: summary([(r, row[m]) for r, row in rows], f"{label}:{m}:{len(rows)}", with_ci) for m in METRICS}


def _outcome_pairs(trials: list[dict], metric: str) -> list[tuple]:
    if metric == "agreement":
        return [(_rep(t), float(t["outcome"] == "agreed")) for t in trials]
    return [(_rep(t), t[metric]) for t in trials]


def _outcomes(trials: list[dict], with_ci: bool, tag: str) -> dict:
    return {m: summary(_outcome_pairs(trials, m), f"{tag}:{m}", with_ci) for m in OUTCOME_METRICS}


# ------------------------------------------------------------ comparisons
def groups_of(doc: dict, done: list[dict]) -> list[dict]:
    """The sweep's groups: one per resource set and persona. Empty for a single-group experiment."""
    cfg = doc["config"]
    envs = cfg.get("environments") or [cfg["environment"]]
    personas = [p["name"] for p in cfg.get("personas") or []] or [None]
    if len(envs) == 1 and len(personas) == 1:
        return []
    out = []
    for e, env in enumerate(envs):
        for p in personas:
            ts = [t for t in done if t.get("env_index", 0) == e and t.get("persona") == p]
            name = " · ".join(filter(None, [env["name"] if len(envs) > 1 else None, p]))
            out.append({"key": f"{e}:{p or ''}", "name": name, "env": env["name"], "persona": p, "trials": ts})
    return out


def sweep(doc: dict, done: list[dict], with_ci: bool) -> Optional[dict]:
    groups = groups_of(doc, done)
    if not groups:
        return None
    labels = list(doc["config"]["agents"])
    base = groups[0]
    rows = []
    for g in groups:
        tag = f"group:{g['key']}:{len(g['trials'])}"
        row = {"key": g["key"], "name": g["name"], "n_negotiations": len(g["trials"]), "outcomes": _outcomes(g["trials"], with_ci, tag),
               "agents": {l: profile(g["trials"], l, with_ci) for l in labels} if g["trials"] else {}, "differences": {}}
        if g is not base:
            for m in OUTCOME_METRICS:
                row["differences"][m] = paired_difference(_rep_means(_outcome_pairs(base["trials"], m)), _rep_means(_outcome_pairs(g["trials"], m)),
                                                          f"diff:{g['key']}:{m}:{len(g['trials'])}", with_ci)
        rows.append(row)
    return {"baseline": base["name"], "groups": rows,
            "note": "Differences are against the first group, paired by repetition (the same seed in each group)."}


def reactions(doc: dict, done: list[dict], with_ci: bool) -> Optional[dict]:
    events = doc["config"].get("events") or []
    if not events:
        return None
    fired = [t for t in done if t.get("events_fired")]
    out = {"events": events, "n_negotiations": len(done), "n_reached": len(fired), "n_ended_before": len(done) - len(fired), "agents": {},
           "pre_event_welfare": summary([(_rep(t), t["pre_event_optimal_welfare"]) for t in fired], "w-pre", with_ci),
           "final_welfare": summary([(_rep(t), t["optimal_welfare"]) for t in fired], "w-final", with_ci)}
    for label in doc["config"]["agents"]:
        rows = [r for r in (event_reaction(t, label) for t in fired) if r]
        responses: dict = {}
        for r in rows:
            responses[r["first_response"] or "none"] = responses.get(r["first_response"] or "none", 0) + 1
        out["agents"][label] = {
            **{k: summary([(r["repetition"], r[k]) for r in rows], f"react:{label}:{k}:{len(rows)}", with_ci) for k in ("before", "after", "shift", "new_resource_claim")},
            "responses": responses, "rows": rows[:200]}
    return out


def usage(doc: dict) -> dict:
    """Totals over every negotiation, aborted ones included. Unknown stays unknown: one missing figure makes the total None."""
    rows = [t["usage"] for t in doc["trials"] if t.get("usage")]
    def total(k):
        vals = [u.get(k) for u in rows if u.get("api_calls")]
        return None if not vals or any(v is None for v in vals) else sum(vals)
    calls = sum(u.get("api_calls") or 0 for u in rows)
    tokens = None if total("input_tokens") is None or total("output_tokens") is None else total("input_tokens") + total("output_tokens")
    return {"calls": calls, "attempts": sum(u.get("api_attempts") or 0 for u in rows), "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
            "tokens": tokens, "cost": total("cost_usd"), "priced": bool(doc["config"].get("prices")),
            "per_negotiation": [{"index": t["index"], "status": t["status"], **{k: t["usage"].get(k) for k in ("api_calls", "input_tokens", "output_tokens", "cost_usd")}} for t in doc["trials"] if t.get("usage")]}


# ------------------------------------------------------------ warnings and findings
def warnings_for(doc: dict, done: list[dict], deterministic: bool) -> list[str]:
    cfg, out = doc["config"], []
    envs = cfg.get("environments") or [cfg["environment"]]
    reps = len({_rep(t) for t in done})
    if done and reps < SMALL_N:
        out.append(f"Small sample: {reps} repetition(s). With fewer than {SMALL_N} per group, intervals are wide and unreliable. Treat every number as a first look.")
    if deterministic and done and all(e["source"] == "custom" for e in envs):
        out.append("These agents follow fixed rules on a fixed resource set, so every repetition replays the same negotiations. More repetitions add no information, "
                   "and a narrow interval here says nothing about how they would do elsewhere. Use the research generator to vary the situation.")
    bad = [t for t in doc["trials"] if t["status"] != "completed"]
    if bad:
        out.append(f"{len(bad)} negotiation(s) did not finish (connection failures, a cap or cancelling). They are not outcomes and are left out of every number.")
    if doc.get("stop_reason"):
        out.append(f"This experiment stopped early. {doc['stop_reason']}")
    planned = doc.get("plan_size", len(doc["trials"]))
    if done and len(doc["trials"]) < planned and doc.get("status") != "running":
        out.append(f"Only {len(doc['trials'])} of the {planned} planned negotiations ran, so groups may be uneven.")
    return out


def _fmt(s: dict, unit: str) -> str:
    if unit == "turns":
        v, ci = f"{s['mean']:.1f}", f"{s['ci'][0]:.1f} to {s['ci'][1]:.1f}" if s["ci"] else None
    else:
        v, ci = f"{s['mean']:.0%}", f"{s['ci'][0]:.0%} to {s['ci'][1]:.0%}" if s["ci"] else None
    return f"{v} (95% CI {ci}, n = {s['n']})" if ci else f"{v} (n = {s['n']}, too few for an interval)"


def _clear(s: dict) -> Optional[bool]:
    """True if the interval excludes 0, False if not, None if there is no interval."""
    return None if not s["ci"] else s["ci"][0] > 0 or s["ci"][1] < 0


def findings(doc: dict, profiles: dict, sweep_: Optional[dict], react: Optional[dict]) -> list[dict]:
    names = {k: d["name"] for k, d in doc["config"]["agents"].items()}
    out = []
    for label, p in profiles.items():
        who = names[label]
        if p["opening_demand"]["n"]:
            out.append({"agent": label, "text": f"{who} opened by asking for {_fmt(p['opening_demand'], 'share')} of the value available to it."})
        if p["share_of_value"]["n"]:
            out.append({"agent": label, "text": f"In agreed deals, {who} received {_fmt(p['share_of_value'], 'share')} of the points the two agents earned."})
        if p["concession_speed"]["n"]:
            s = p["concession_speed"]
            clear = _clear(s)
            verdict = ("its demand fell from offer to offer" if s["mean"] > 0 else "its demand rose from offer to offer") if clear else "these runs cannot show a clear direction in its demand"
            out.append({"agent": label, "text": f"Per offer, {who}'s demand changed by {-s['mean']:+.1%} of its maximum (n = {s['n']}): {verdict}."})
        for m, phrase in (("walk_away_rate", "walked away in"), ("rule_break_rate", "broke the rules in")):
            if p[m]["mean"]:
                out.append({"agent": label, "text": f"{who} {phrase} {_fmt(p[m], 'rate')} of negotiations."})
        if p["reveals_preferences"]["n"] and p["reveals_preferences"]["mean"]:
            out.append({"agent": label, "text": f"By a simple text pattern, {_fmt(p['reveals_preferences'], 'share')} of {who}'s messages stated a preference. This cannot tell truth from bluff."})
    if react and react["n_reached"]:
        for label, r in react["agents"].items():
            s = r["shift"]
            if s["n"]:
                clear = _clear(s)
                tail = ("In these runs its demand " + ("rose." if s["mean"] > 0 else "fell.")) if clear else "These runs cannot show a clear change."
                out.append({"agent": label, "text": f"After the new resource arrived, {names[label]}'s demand changed by {s['mean']:+.0%} of its maximum (n = {s['n']}). {tail}"})
        if react["n_ended_before"]:
            out.append({"agent": None, "text": f"{react['n_ended_before']} of {react['n_negotiations']} negotiations ended before the event, so they show no reaction to it."})
    if sweep_:
        for g in sweep_["groups"][1:]:
            d = g["differences"]["agreement"]
            if d["n"]:
                clear = _clear(d)
                tail = "In these runs the rates differ." if clear else "These runs cannot tell the two apart."
                out.append({"agent": None, "text": f"Deals reached on {g['name']} minus {sweep_['baseline']}: {d['mean']:+.0%} (n = {d['n']} paired repetitions). {tail}"})
    return out


# ------------------------------------------------------------ entry point
def analyse(doc: dict, done: list[dict], deterministic: bool) -> dict:
    cfg = doc["config"]
    with_ci = doc.get("status") != "running"  # intervals when the experiment has finished; means while it runs
    labels = list(cfg["agents"])
    profiles = {l: profile(done, l, with_ci) for l in labels} if done else {}
    sweep_, react = sweep(doc, done, with_ci), reactions(doc, done, with_ci)
    return {"exploratory": EXPLORATORY, "data_label": cfg["label"], "source_labels": cfg.get("source_labels"), "small_n_threshold": SMALL_N,
            "metrics": {k: {"label": v[0], "unit": v[1], "definition": v[2]} for k, v in {**METRICS, **OUTCOME_METRICS}.items()},
            "outcomes": _outcomes(done, with_ci, f"all:{len(done)}") if done else None, "profiles": profiles, "sweep": sweep_, "reactions": react,
            "usage": usage(doc), "warnings": warnings_for(doc, done, deterministic), "findings": findings(doc, profiles, sweep_, react),
            "n_completed": len(done), "n_repetitions": len({_rep(t) for t in done}), "intervals_ready": with_ci}


def metric_rows(doc: dict) -> list[list]:
    """One row per negotiation, agent and metric: the exported numbers every chart can be recomputed from."""
    from workbench.simulations import _environments
    source = doc["config"].get("source_labels") or {}
    rows = []
    for t in doc["trials"]:
        if t["status"] != "completed":
            continue
        for label in doc["config"]["agents"]:
            for metric, value in trial_metrics(t, label).items():
                rows.append([source.get(label, doc["config"]["label"].split(":")[0]), t["index"], _rep(t), t["seed"],
                             _environments(doc["config"])[t.get("env_index", 0)]["name"], t.get("persona") or "", t["condition"], label,
                             doc["config"]["agents"][label]["name"], metric, value])
    return rows


METRIC_ROW_HEADER = ("data_source", "negotiation", "repetition", "seed", "resource_set", "persona", "condition", "agent", "agent_name", "metric", "value")
