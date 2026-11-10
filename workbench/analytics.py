"""Behavioural analytics for a simulation, computed from its stored negotiations only.

Three kinds of statement are kept apart:
  ground truth  the private points each agent was given (known to the evaluator),
  observed      what the agent did: its offers, concessions, accepts and walk-aways,
  derived       a calculation over observed actions and ground truth (each states its
                definition, denominator and n),
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


def _rank_agreement(points: dict, shares: dict) -> Optional[tuple]:
    """Over resource pairs with different private points: (share of pairs where the agent claimed a
    strictly larger share of the one worth more to it, share where it claimed equal shares).
    Ties are reported separately, so a uniform claimer is not counted as aligned. None if no pair differs."""
    pairs = [(a, b) if points[a] > points[b] else (b, a) for a, b in combinations(points, 2) if points[a] != points[b]]
    if not pairs:
        return None
    return (sum(shares[hi] > shares[lo] for hi, lo in pairs) / len(pairs),
            sum(shares[hi] == shares[lo] for hi, lo in pairs) / len(pairs))


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
        "n_concession": sum(x["total_concession"] is not None for x in b),
        "accepted": accepts, "walked_away": walks, "invalid": invalid,
        "priority_alignment": _m(x[0] for x in alignment if x), "priority_ties": _m(x[1] for x in alignment if x),
        "n_alignment": sum(x is not None for x in alignment),
        "resources": by_res,
    }


# ------------------------------------------------------------ derived behavioural measures
def _spearman(xs: list, ys: list) -> Optional[float]:
    """Spearman rank correlation (average ranks); None if either side is constant or n < 3."""
    from src.evaluation.inference import _ranks
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den


SCARCITY_DEF = ("Share of a resource's units the agent claimed in its first offer, against that resource's quantity in the "
                "instance (fewer units = scarcer). Unit and denominator: (negotiation, resource) pairs in which the agent made an "
                "opening offer. Bins are terciles of the quantities observed in this simulation. Caveat: each agent spreads 100 points over "
                "a whole resource, so a scarcer resource is worth more per unit; quantity and per-unit value are confounded here.")


def scarcity_response(label: str, trials: list[dict]) -> dict:
    pts = [(t["pool"][c], t["agents"][label]["opening"]["shares"][c]) for t in trials if t["agents"][label]["opening"] for c in t["pool"]]
    out = {"definition": SCARCITY_DEF, "n": len(pts), "distinct_quantities": len({q for q, _ in pts})}
    if out["distinct_quantities"] < 3:
        return {**out, "measurable": False,
                "reason": "Fewer than three distinct quantities were observed, so scarcity barely varied. Use the research "
                          "generator, which draws new quantities for every seed."}
    qs = sorted(q for q, _ in pts)
    cut1, cut2 = qs[len(qs) // 3], qs[2 * len(qs) // 3]
    bins = {"scarce": [p for p in pts if p[0] < cut1], "middle": [p for p in pts if cut1 <= p[0] < cut2],
            "plentiful": [p for p in pts if p[0] >= cut2]}
    return {**out, "measurable": True, "spearman_quantity_vs_claim": _spearman([q for q, _ in pts], [s for _, s in pts]),
            "bins": {k: {"quantity_range": [min(q for q, _ in v), max(q for q, _ in v)] if v else None, "n": len(v),
                         "opening_claim_mean": _m(s for _, s in v)} for k, v in bins.items()}}


def concession_curve(label: str, trials: list[dict]) -> dict:
    by_k: dict = {}
    for t in trials:
        seat = t["agents"][label]["seat"]
        own = [e[f"value_{seat}"] / 100 for e in t["events"]
               if e["actor"] == seat and e["action"] == "OFFER" and e.get(f"value_{seat}") is not None]
        for k, v in enumerate(own, start=1):
            by_k.setdefault(k, []).append(v)
    return {"definition": "Mean value of the agent's k-th offer, valued at its own private points / 100. "
                          "n_k = negotiations in which it made at least k offers (later offers come from longer negotiations).",
            "points": [{"k": k, "n": len(v), "mean_value": mean(v)} for k, v in sorted(by_k.items())]}


def value_split(label: str, trials: list[dict]) -> dict:
    hi, lo, used = [], [], []
    for t in trials:
        a = t["agents"][label]
        if not a["opening"]:
            continue
        pts = t["points"][a["seat"]]
        if len(set(pts.values())) < 2:
            continue
        ranked = sorted(pts, key=lambda c: (-pts[c], c))
        top = set(ranked[: len(ranked) // 2])
        hi += [a["opening"]["shares"][c] for c in top]
        lo += [a["opening"]["shares"][c] for c in pts if c not in top]
        used.append(t["index"])
    h, l = _m(hi), _m(lo)
    return {"definition": "Mean opening claim share on the resources worth most to the agent (the top half by its own private "
                          "points; with an odd count the middle one goes to the rest) minus the same on the rest. Denominator: "
                          "negotiations with an opening offer by the agent and unequal points.",
            "n_negotiations": len(used), "high_value_claim": h, "low_value_claim": l,
            "difference": None if h is None or l is None else h - l, "evidence": [{"trial": i} for i in used[:12]]}


def _role_rates(label: str, trials: list[dict]) -> dict:
    n = len(trials)
    return {"n": n, "agreement_rate": sum(t["outcome"] == "agreed" for t in trials) / n if n else None,
            "utility_mean": _m(t["agents"][label]["utility"] for t in trials),
            "outcomes": {o: sum(t["outcome"] == o for t in trials) for o in OUTCOMES}}


def by_role(label: str, trials: list[dict]) -> dict:
    """Outcomes and the agent's own utility (its points / 100, failures 0) by whether it opened and by seat."""
    seat = lambda t: t["agents"][label]["seat"]  # noqa: E731
    return {"opens": _role_rates(label, [t for t in trials if t["first_mover"] == seat(t)]),
            "responds": _role_rates(label, [t for t in trials if t["first_mover"] != seat(t)]),
            "seat_A": _role_rates(label, [t for t in trials if seat(t) == "A"]),
            "seat_B": _role_rates(label, [t for t in trials if seat(t) == "B"])}


def by_condition_agent(label: str, trials: list[dict], conditions: list[str]) -> dict:
    out = {}
    for c in conditions:
        ts = [t for t in trials if t["condition"] == c]
        b = [t["agents"][label]["behavior"] for t in ts]
        out[c] = {**_role_rates(label, ts), "opening_demand_mean": _m(x["opening_demand"] for x in b),
                  "total_concession_mean": _m(x["total_concession"] for x in b),
                  "n_concession": sum(x["total_concession"] is not None for x in b)}
    return out


def exploratory_inference(doc: dict, done: list[dict]) -> dict:
    """The preregistered §8.9 procedures applied to this simulation, as an EXPLORATORY description.
    Instance = the actual negotiation instance: the seed for generated environments, and a single
    instance for a fixed environment (its repetitions replay the same instance)."""
    from src.evaluation import inference as I
    if set(doc["config"]["conditions"]) != {I.BASELINE, I.STRUCTURED}:
        return {"available": False, "reason": "Needs both conditions, baseline_v1 and structured_v1, in the design."}
    if len(doc["config"].get("environments") or []) > 1 or doc["config"].get("personas"):
        return {"available": False, "reason": "A sweep mixes several resource sets or personas in each condition. See the comparison between groups instead."}
    fixed = doc["config"]["environment"]["source"] != "generated"
    rows = [{"seed": "fixed" if fixed else t["seed"], "condition": t["condition"], "outcome": t["outcome"],
             "rwe": t["rwe"], "ew": t["ew"]} for t in done]
    res = I.analyse_cached(rows)
    return {"available": True, "fixed_environment": fixed, "result": res,
            "interpretations": {h: I.interpret(r) for h, r in res["primary"].items()},
            "scope": "Exploratory: the preregistered procedures applied to this simulation. It describes these agents on these "
                     "instances only; it is not the confirmatory analysis and says nothing about any LLM."
                     + (" A fixed environment is one instance however many repetitions ran, so there is at most one pair." if fixed else "")}


def observations(profiles: dict, by_condition: dict, names: dict, reads_instructions: dict,
                 behaviour: Optional[dict] = None) -> list[dict]:
    """Descriptive, evidence-backed statements. Each names its basis and the negotiations behind it."""
    out = []
    for label, p in profiles.items():
        who = f"{names[label]} ({label})"
        claimed = {c: r for c, r in p["resources"].items() if r["opening_claim_mean"] is not None}
        if claimed:
            top = max(claimed, key=lambda c: (claimed[c]["opening_claim_mean"], c))
            low = min(claimed, key=lambda c: (claimed[c]["opening_claim_mean"], c))
            r = claimed[top]
            if round(r["opening_claim_mean"], 2) == round(claimed[low]["opening_claim_mean"], 2):
                text = f"In its opening offers, {who} claimed about the same share of every resource ({r['opening_claim_mean']:.0%} on average)"
            else:
                text = (f"In its opening offers, {who} claimed its largest share of {top} ({r['opening_claim_mean']:.0%} on average) "
                        f"and its smallest share of {low} ({claimed[low]['opening_claim_mean']:.0%})")
            out.append({"agent": label, "basis": "observed", "text": text + ".",
                        "evidence": r["opening_evidence"][:12], "n": r["n_opening"]})
        if p["priority_alignment"] is not None:
            out.append({"agent": label, "basis": "observed vs ground truth",
                        "text": f"Averaged over its opening offers, in {p['priority_alignment']:.0%} of resource pairs {who}'s claim was "
                                f"strictly larger on the resource its private points (for the whole resource) rated higher; in "
                                f"{p['priority_ties']:.0%} it claimed equal shares "
                                f"({p['n_alignment']} opening offers).",
                        "evidence": [e for r in p["resources"].values() for e in r["opening_evidence"]][:12], "n": p["n_alignment"]})
        bh = (behaviour or {}).get(label)
        if bh and bh["value_split"]["difference"] is not None:
            v = bh["value_split"]
            out.append({"agent": label, "basis": "observed vs ground truth",
                        "text": f"{who} opened by claiming {v['high_value_claim']:.0%} of the resources worth most to it and "
                                f"{v['low_value_claim']:.0%} of the rest ({v['n_negotiations']} negotiations).",
                        "evidence": v["evidence"], "n": v["n_negotiations"]})
        if bh and bh["scarcity"]["measurable"] and bh["scarcity"]["spearman_quantity_vs_claim"] is not None:
            sc = bh["scarcity"]
            out.append({"agent": label, "basis": "derived",
                        "text": f"Across {sc['n']} (negotiation, resource) pairs, the Spearman correlation between a resource's quantity "
                                f"and {who}'s opening claim share was {sc['spearman_quantity_vs_claim']:+.2f} (negative: a larger "
                                f"share of scarcer resources; scarcity is confounded with per-unit value).", "evidence": [], "n": sc["n"]})
        if p["total_concession_mean"] is not None:
            c = p["total_concession_mean"]  # negative = it asked for more over time (hardened)
            out.append({"agent": label, "basis": "observed",
                        "text": f"Between its first and last offer, {who} {'lowered' if c >= 0 else 'raised'} what it asked for by "
                                f"{abs(c):.2f} of its own maximum on average (negotiations where it made at least two offers).",
                        "evidence": [], "n": p["n_concession"]})
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
    conds = doc["config"]["conditions"]
    profiles = {label: agent_profile(label, done) for label in "XY"} if done else {}
    behaviour = {label: {"scarcity": scarcity_response(label, done), "concession_curve": concession_curve(label, done),
                         "value_split": value_split(label, done), "by_role": by_role(label, done),
                         "by_condition": by_condition_agent(label, done, conds)} for label in "XY"} if done else {}
    by_condition = {c: _rates([t for t in done if t["condition"] == c]) for c in conds}
    names = {k: d["name"] for k, d in agents.items()}
    reads = {k: KINDS[d["kind"]].reads_instructions for k, d in agents.items()}
    deterministic = all(KINDS[d["kind"]].deterministic for d in agents.values())
    from sandbox.analysis import analyse as sandbox_analyse
    return {
        "sandbox": sandbox_analyse(doc, done, deterministic),
        "label": doc["config"]["label"], "n_completed": len(done), "not_outcomes": dict(skipped),
        "overall": _rates(done) if done else None, "by_condition": by_condition,
        "by_first_mover": {s: _rates([t for t in done if t["first_mover"] == s]) for s in "AB"},
        "agents": profiles, "behaviour": behaviour,
        "observations": observations(profiles, by_condition, names, reads, behaviour) if done else [],
        "inference": ({"available": False, "reason": "No completed negotiations."} if not done
                      else {"available": False, "reason": "Computed when the simulation finishes (it is still running)."}
                      if doc.get("status") == "running" else exploratory_inference(doc, done)),
        "not_available": [*(["Both agents follow fixed deterministic rules: differences across seats, openers or instances come from "
                             "the rules meeting different situations, not from learning or adaptation."] if deterministic else []),
                          "Motivations are never inferred: agents' hidden reasoning is unknown."],
    }
