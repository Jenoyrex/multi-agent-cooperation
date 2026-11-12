"""The report in plain words: one place that turns an experiment's stored numbers into sentences a non-specialist can
read. The report page and report.md both use it, so they always say the same thing.

Rules: every sentence comes from a number that is also shown in full in the report's details; ranges are the same 95%
intervals the analysis computes; a pattern the data cannot show clearly is called unclear, never a trend; nothing says
why an agent acted.
"""
from __future__ import annotations

import math
from typing import Optional

from sandbox import analysis as SA

INVALID_WORDS = {
    "invalid_allocation": "offered a split that was not possible (it did not match what was on the table)",
    "malformed_output": "replied in a form the lab could not read as a move",
    "illegal_accept": "tried to accept when there was no offer to accept",
}
OUTCOME_WORDS = {"agreed": "reached a deal", "walked_away": "ended with a walk-away", "timeout": "ran out of turns",
                 "invalid_action": "ended because an agent broke the rules"}
SOURCE_WORDS = {
    "simulated": "Both agents follow fixed rules written in code, so this shows how the lab measures behaviour. It says nothing about any AI model.",
    "local-model": "At least one agent is an AI model running on this computer. Its answers can vary from run to run.",
    "api-model": "At least one agent is an AI model reached through a provider's API. Its answers can vary from run to run.",
    "local-llm": "At least one agent is an AI model running on this computer. Its answers can vary from run to run.",
}
GLOSSARY = [
    ("Negotiation", "One round of bargaining between the two agents, from the first offer until a deal, a walk-away, the turn limit or a broken rule."),
    ("Deal reached", "Both agents agreed on how to split everything on the table."),
    ("Points", "Each agent privately values the resources, 100 points in total. An agent's points are what its share of the deal is worth to it."),
    ("Best result possible", "The split that gives the two agents the most points together. The lab computes it from both agents' private values; the agents never see it."),
    ("Value created", "The agents' points together, as a share of the best result possible. A failed negotiation counts as 0."),
    ("Fairness (worse-off side)", "The points of whichever agent did worse, out of its 100. A failed negotiation counts as 0. Higher means even the weaker side did well."),
    ("Deal quality", "Like value created, but only over negotiations that reached a deal."),
    ("Evenness", "In deals, how close the two agents' points were: 100% means exactly equal."),
    ("Envy-free", "A deal where neither agent would rather have the other's share, judged by its own values."),
    ("Broke the rules", "An agent made a move the rules do not allow, such as an impossible split. It ends the negotiation and is recorded, never corrected."),
    ("Repetition", "One full set of negotiations with the same seed. More repetitions give firmer numbers."),
    ("Likely range (95% interval)", "If the experiment were repeated, the average would usually land in this range. It comes from the repetitions run here, so few repetitions give a wide range."),
    ("Condition", "The instructions both agents were given: 'Rules only', or 'Rules plus tips' with negotiation advice."),
    ("Seat and opener", "Seats A and B hold different private values, and one side makes the first offer. Swapping them makes the comparison fair."),
]


def pct(x: Optional[float], digits: int = 0) -> str:
    """Half rounds up, as the page's own figures do (12.5% shows as 13% in both)."""
    if x is None:
        return "—"
    scale = 10 ** digits
    return f"{math.floor(x * 100 * scale + 0.5) / scale:.{digits}f}%"


def _range(s: dict, unit: str = "share") -> str:
    """'likely between 33% and 38% (3 repetitions)' or 'too few repetitions for a range'."""
    show = (lambda v: f"{v:.1f}") if unit == "turns" else pct
    reps = f"{s['n']} repetition{'s' if s['n'] != 1 else ''}"
    if s.get("ci"):
        lo, hi = show(s["ci"][0]), show(s["ci"][1])
        return f"{lo} in every repetition ({reps})" if lo == hi else f"likely between {lo} and {hi}, from {reps}"
    return f"too few repetitions for a likely range ({reps})"


def summary(doc: dict, a: dict) -> dict:
    """{headline, points, numbers, agents, caveats, glossary}: plain words for one experiment, from its analytics."""
    cfg, sb = doc["config"], a["sandbox"]
    names = {k: v["name"] for k, v in cfg["agents"].items()}
    done = [t for t in doc["trials"] if t["status"] == "completed"]
    n = len(done)
    counts = {o: sum(t["outcome"] == o for t in done) for o in OUTCOME_WORDS}
    ov = a.get("overall") or {}
    X, Y = names.get("X", "Agent X"), names.get("Y", "Agent Y")
    points: list[dict] = []
    if not n:
        return {"headline": "No negotiation has finished yet, so there is nothing to summarise.", "points": [], "numbers": [],
                "agents": [], "caveats": [SOURCE_WORDS.get(cfg.get("data_kind"), "")], "glossary": GLOSSARY}

    headline = (f"{X} and {Y} reached a deal in {counts['agreed']} of {n} negotiation{'s' if n != 1 else ''} "
                f"({pct(counts['agreed'] / n)}).")
    fails = [f"{counts[o]} {OUTCOME_WORDS[o]}" for o in ("walked_away", "timeout", "invalid_action") if counts[o]]
    if fails:
        points.append({"text": "The others: " + "; ".join(fails) + ".", "detail": "Counts of how each negotiation ended (see 'How negotiations ended')."})

    prof = sb.get("profiles") or {}
    share = {k: p["share_of_value"] for k, p in prof.items() if p["share_of_value"]["mean"] is not None}
    if len(share) == 2:
        (ka, sa), (kb, sb_) = sorted(share.items(), key=lambda kv: -kv[1]["mean"])
        even = abs(sa["mean"] - 0.5) < 0.03
        points.append({"text": (f"When they made a deal, the points were split about evenly: {names[ka]} {pct(sa['mean'])}, {names[kb]} {pct(sb_['mean'])}."
                                if even else f"When they made a deal, {names[ka]} usually came out ahead: about {pct(sa['mean'])} of the points, "
                                             f"against {pct(sb_['mean'])} for {names[kb]}."),
                       "detail": f"Share of the points in agreed deals. {names[ka]}: {_range(sa)}. {names[kb]}: {_range(sb_)}."})
    opening = {k: p["opening_demand"] for k, p in prof.items() if p["opening_demand"]["mean"] is not None}
    if len(opening) == 2:
        (ka, sa), (kb, sb_) = sorted(opening.items(), key=lambda kv: -kv[1]["mean"])
        points.append({"text": f"{names[ka]} started bolder, asking for {pct(sa['mean'])} of what it could get in its first offer; {names[kb]} asked for {pct(sb_['mean'])}.",
                       "detail": f"Opening demand. {names[ka]}: {_range(sa)}. {names[kb]}: {_range(sb_)}."})
    if ov.get("rwe") is not None:
        points.append({"text": f"Together they captured {pct(ov['rwe'])} of the best result possible, counting failed negotiations as zero.",
                       "detail": f"Value created (relative welfare efficiency, RWE) = {ov['rwe']:.3f}. In deals only, it was {pct(ov.get('swe'))}."})
    if ov.get("ew") is not None:
        points.append({"text": f"On average, the side that did worse ended with {pct(ov['ew'])[:-1]} of its 100 points.",
                       "detail": f"Fairness (egalitarian welfare, EW) = {ov['ew']:.3f}, failures counted as 0."})
    if counts["invalid_action"]:
        reasons: dict = {}
        for t in done:
            if t["outcome"] == "invalid_action":
                reasons[t.get("invalid_reason")] = reasons.get(t.get("invalid_reason"), 0) + 1
        k = counts["invalid_action"]
        said = (f"an agent {INVALID_WORDS.get(next(iter(reasons)), 'broke a rule')}" if len(reasons) == 1 else
                "; ".join(f"{v} time{'s' if v != 1 else ''} an agent {INVALID_WORDS.get(r, r or 'broke a rule')}" for r, v in reasons.items()))
        points.append({"text": f"Why {'those' if k != 1 else 'that'} {k} broke the rules: {said}. "
                               "The lab records the move as it happened and never corrects it.",
                       "detail": "Reasons: " + ", ".join(f"{r} × {v}" for r, v in reasons.items()) + "."})
    r = sb.get("reactions")
    if r:
        ev = (r.get("events") or [{}])[0]
        line = (f"A surprise resource ({ev.get('units', '?')} units of {ev.get('category', '?')}) appeared at turn {ev.get('turn', '?')}; "
                f"{r['n_reached']} of {r['n_negotiations']} negotiations lasted long enough to see it.")
        claims = [(names[k], v["new_resource_claim"]) for k, v in r["agents"].items() if v["new_resource_claim"]["mean"] is not None]
        if claims:
            line += " Afterwards " + " and ".join(f"{nm} claimed about {pct(c['mean'])} of it" for nm, c in claims) + "."
        points.append({"text": line, "detail": "See 'Reaction to resource events' in the full details for before/after demands with ranges."})
    if sb.get("sweep"):
        groups = sb["sweep"]["groups"]
        points.append({"text": "Deals reached in each group: " + "; ".join(f"{g['name']} {pct(g['outcomes']['agreement']['mean'])}" for g in groups) + ".",
                       "detail": "Differences between groups, with likely ranges, are under 'Comparison between groups'."})
    turns = ov.get("mean_rounds")
    if turns is not None:
        points.append({"text": f"A negotiation took {turns:.1f} turns on average.", "detail": "Turns are counted across both agents."})

    numbers = [
        {"label": "Deals reached", "value": pct(counts["agreed"] / n), "meaning": f"{counts['agreed']} of {n} negotiations ended in a deal.", "exact": f"agreement rate {counts['agreed'] / n:.3f}"},
        {"label": "Best result captured", "value": pct(ov.get("rwe")), "meaning": "Their points together, as a share of the best split possible (failures count as 0).", "exact": f"RWE {ov['rwe']:.3f}" if ov.get("rwe") is not None else "—"},
        {"label": "Worse-off side's points", "value": "—" if ov.get("ew") is None else f"{pct(ov['ew'])[:-1]} / 100", "meaning": "How well the weaker side did, on average (failures count as 0).", "exact": f"EW {ov['ew']:.3f}" if ov.get("ew") is not None else "—"},
        {"label": "Turns per negotiation", "value": "—" if turns is None else f"{turns:.1f}", "meaning": "Average length, counting both agents' moves.", "exact": "—" if turns is None else f"mean rounds {turns:.2f}"},
        {"label": "Walk-aways", "value": pct(counts["walked_away"] / n), "meaning": f"{counts['walked_away']} negotiation(s) ended with an agent leaving.", "exact": f"{counts['walked_away']} of {n}"},
        {"label": "Broke the rules", "value": pct(counts["invalid_action"] / n), "meaning": f"{counts['invalid_action']} negotiation(s) ended with an invalid move.", "exact": f"{counts['invalid_action']} of {n}"},
    ]

    agents = []
    for k, p in prof.items():
        who, lines = names[k], []
        if p["opening_demand"]["mean"] is not None:
            lines.append(f"First offer: asked for {pct(p['opening_demand']['mean'])} of what it could get ({_range(p['opening_demand'])}).")
        if p["share_of_value"]["mean"] is not None:
            lines.append(f"In deals: ended with {pct(p['share_of_value']['mean'])} of the points the two earned ({_range(p['share_of_value'])}). 50% would be an even split.")
        c = p["concession_speed"]
        if c["n"]:
            lines.append(("Gave ground offer by offer." if c["mean"] > 0 else "Asked for more as it went on.") if SA._clear(c)
                         else "No clear pattern in how its demands changed between offers.")
        if p["offer_fairness"]["mean"] is not None:
            lines.append(f"Its offers were {pct(p['offer_fairness']['mean'])} even on average (100% = both sides equally well off).")
        for m, verb in (("walk_away_rate", "Walked away"), ("rule_break_rate", "Broke the rules")):
            if p[m]["mean"]:
                lines.append(f"{verb} in {pct(p[m]['mean'])} of negotiations.")
        if p["reveals_preferences"]["mean"]:
            lines.append(f"About {pct(p['reveals_preferences']['mean'])} of its messages said what it wanted (a simple text check; it cannot tell truth from bluff).")
        agents.append({"label": k, "name": who, "source": (sb.get("source_labels") or {}).get(k, ""), "lines": lines})

    caveats = [SOURCE_WORDS.get(cfg.get("data_kind"), "These results describe the agents that answered, in these runs.")]
    reps = sb.get("n_repetitions") or cfg.get("repetitions")
    caveats.append(f"Based on {reps} repetition{'s' if reps != 1 else ''} ({n} negotiations): a first look, not proof."
                   + (" With fewer than 10 repetitions the likely ranges are wide." if reps and reps < sb.get("small_n_threshold", 10) else ""))
    caveats.append("These numbers describe what the agents did, never why they did it. They are not the research study's results.")
    return {"headline": headline, "points": points, "numbers": numbers, "agents": agents, "caveats": caveats, "glossary": GLOSSARY}
