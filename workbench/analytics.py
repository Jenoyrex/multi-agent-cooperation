"""Behavioural analytics for a simulation, computed from its stored negotiations only.

Three kinds of statement are kept apart:
  ground truth  the private points each agent was given (known to the evaluator),
  observed      what the agent did: its offers, concessions, accepts and walk-aways,
  inferred      never produced. Nothing here claims to know why an agent acted.

Conventions follow the research metrics (spec §8.6): RWE and egalitarian welfare
score failures as 0 and use every negotiation; SWE, equitability and envy-freeness use
agreed negotiations only and report their own n. Aborted or cancelled negotiations are
not outcomes and are excluded, with their count reported.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from statistics import mean, median
from typing import Optional

OUTCOMES = ("agreed", "walked_away", "timeout", "invalid_action")


def _m(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def _rates(trials: list[dict]) -> dict:
    n = len(trials)
    agreed = [t for t in trials if t["outcome"] == "agreed"]
    envy = [t["envy_free"] for t in agreed if t["envy_free"] is not None]
    return {
        "n": n, "n_agreed": len(agreed), "outcomes": {o: sum(t["outcome"] == o for t in trials) for o in OUTCOMES},
        "agreement_rate": len(agreed) / n if n else None,
        "rwe": _m(t["rwe"] for t in trials), "ew": _m(t["ew"] for t in trials),
        "sw": _m(t["sw"] for t in agreed), "swe": _m(t["swe"] for t in agreed),
        "equitability": _m(t["equitability"] for t in agreed),
        "envy_free_rate": sum(envy) / len(envy) if envy else None,
        "invalid_rate": sum(t["outcome"] == "invalid_action" for t in trials) / n if n else None,
        "invalid_reasons": dict(Counter(t["invalid_reason"] for t in trials if t["invalid_reason"])),
        "mean_rounds": _m(t["rounds"] for t in trials),
    }


def _rank_agreement(points: dict, shares: dict) -> Optional[float]:
    """Share of resource pairs with different points where the agent claimed at least as
    large a share of the one worth more to it. None if no pair differs."""
    pairs = [(a, b) if points[a] > points[b] else (b, a) for a, b in combinations(points, 2) if points[a] != points[b]]
    return sum(shares[hi] >= shares[lo] for hi, lo in pairs) / len(pairs) if pairs else None


def agent_profile(label: str, trials: list[dict]) -> dict:
    mine = [(t, t["agents"][label]) for t in trials]
    resources = sorted({c for t in trials for c in t["pool"]})
    by_res = {}
    for c in resources:
        rows = [(t, a) for t, a in mine if c in t["pool"]]
        opening = [(t, a["opening"]["shares"][c]) for t, a in rows if a["opening"]]
        final = [a["final_shares"][c] for t, a in rows if a["final_shares"]]
        pts = [t["points"][a["seat"]][c] for t, a in rows]
        by_res[c] = {"points_mean": _m(pts), "opening_claim_mean": _m(s for _, s in opening),
                     "final_share_mean": _m(final), "n_opening": len(opening), "n_final": len(final),
                     "opening_evidence": [{"trial": t["index"], "turn": t["agents"][label]["opening"]["turn"]} for t, _ in opening]}
    alignment = [_rank_agreement(t["points"][a["seat"]], a["opening"]["shares"]) for t, a in mine if a["opening"]]
    b = [a["behavior"] for _, a in mine]
    accepts = [t["index"] for t, a in mine if t["outcome"] == "agreed" and t["events"] and t["events"][-1]["actor"] == a["seat"]]
    walks = [t["index"] for t, a in mine if t["outcome"] == "walked_away" and a["ended"]]
    invalid = [t["index"] for t, a in mine if t["outcome"] == "invalid_action" and a["ended"]]
    utilities = [{"trial": t["index"], "utility": a["utility"], "condition": t["condition"]} for t, a in mine]
    return {
        "label": label, "negotiations": len(mine), "seat_A": sum(a["seat"] == "A" for _, a in mine),
        "utility_mean": _m(u["utility"] for u in utilities), "utility_median": median([u["utility"] for u in utilities]) if utilities else None,
        "utilities": utilities,
        "opening_demand_mean": _m(x["opening_demand"] for x in b), "total_concession_mean": _m(x["total_concession"] for x in b),
        "concession_frequency_mean": _m(x["concession_frequency"] for x in b), "offers_mean": _m(x["offers"] for x in b),
        "accepted": accepts, "walked_away": walks, "invalid": invalid,
        "priority_alignment": _m(alignment), "n_alignment": sum(x is not None for x in alignment),
        "resources": by_res,
    }


def observations(profiles: dict, by_condition: dict, names: dict, reads_instructions: dict) -> list[dict]:
    """Descriptive, evidence-backed statements. Each names its basis and the negotiations behind it."""
    out = []
    for label, p in profiles.items():
        who = f"{names[label]} ({label})"
        claimed = {c: r for c, r in p["resources"].items() if r["opening_claim_mean"] is not None}
        if claimed:
            top = max(claimed, key=lambda c: (claimed[c]["opening_claim_mean"], c))
            low = min(claimed, key=lambda c: (claimed[c]["opening_claim_mean"], c))
            r = claimed[top]
            text = f"{who} claimed {r['opening_claim_mean']:.0%} of {top} on average in its opening offers"
            if low != top:
                text += f", and {claimed[low]['opening_claim_mean']:.0%} of {low}"
            out.append({"agent": label, "basis": "observed", "text": text + ".",
                        "evidence": r["opening_evidence"][:12], "n": r["n_opening"]})
        if p["priority_alignment"] is not None:
            out.append({"agent": label, "basis": "observed vs ground truth",
                        "text": f"In {p['priority_alignment']:.0%} of resource pairs, {who}'s opening claim was at least as large "
                                f"on the resource its private points rated higher ({p['n_alignment']} opening offers).",
                        "evidence": [e for r in p["resources"].values() for e in r["opening_evidence"]][:12], "n": p["n_alignment"]})
        if p["total_concession_mean"] is not None:
            out.append({"agent": label, "basis": "observed",
                        "text": f"Between its first and last offer, {who} lowered what it asked for by {p['total_concession_mean']:.2f} "
                                f"of its own maximum on average (negotiations where it made at least two offers).",
                        "evidence": [], "n": p["negotiations"]})
        if p["accepted"] or p["walked_away"] or p["invalid"]:
            parts = [f"accepted the final offer in {len(p['accepted'])}"] + \
                    ([f"walked away in {len(p['walked_away'])}"] if p["walked_away"] else []) + \
                    ([f"ended {len(p['invalid'])} with an invalid action"] if p["invalid"] else [])
            out.append({"agent": label, "basis": "observed", "text": f"{who} {', '.join(parts)} of {p['negotiations']} negotiations.",
                        "evidence": [{"trial": i} for i in (p["accepted"] + p["walked_away"] + p["invalid"])[:12]], "n": p["negotiations"]})
    conds = [c for c, v in by_condition.items() if v["n"]]
    if len(conds) == 2:
        a, b = (by_condition[c] for c in conds)
        same = (a["agreement_rate"], a["rwe"], a["ew"]) == (b["agreement_rate"], b["rwe"], b["ew"])
        deaf = not any(reads_instructions.values())
        text = (f"Agreement rate {a['agreement_rate']:.0%} vs {b['agreement_rate']:.0%} and mean RWE {a['rwe']:.3f} vs {b['rwe']:.3f} "
                f"for {conds[0]} vs {conds[1]}.")
        if deaf:
            text += (" Neither agent reads instructions, so the condition label cannot change its behaviour"
                     + ("; the results are identical, as expected." if same else "; any difference comes from elsewhere in the design."))
        out.append({"agent": None, "basis": "observed (descriptive, no test)", "text": text, "evidence": [], "n": a["n"] + b["n"]})
    return out


def analyse(doc: dict) -> dict:
    from workbench.agents import KINDS  # local: keeps this module importable without the agent registry
    done = [t for t in doc["trials"] if t["status"] == "completed"]
    skipped = Counter(t["status"] for t in doc["trials"] if t["status"] != "completed")
    agents = doc["config"]["agents"]
    profiles = {label: agent_profile(label, done) for label in "XY"} if done else {}
    by_condition = {c: _rates([t for t in done if t["condition"] == c]) for c in doc["config"]["conditions"]}
    names = {k: d["name"] for k, d in agents.items()}
    reads = {k: KINDS[d["kind"]].reads_instructions for k, d in agents.items()}
    return {
        "label": doc["config"]["label"], "n_completed": len(done), "not_outcomes": dict(skipped),
        "overall": _rates(done) if done else None, "by_condition": by_condition,
        "by_first_mover": {s: _rates([t for t in done if t["first_mover"] == s]) for s in "AB"},
        "agents": profiles, "observations": observations(profiles, by_condition, names, reads) if done else [],
        "not_available": ["Responses to changes in resource scarcity are not computed yet.",
                          "Inferential tests are not run: the analysis is descriptive.",
                          "Motivations are never inferred: agents' hidden reasoning is unknown."],
    }
