"""Environments: declarative scenario definitions for two-agent negotiation.

An environment is data, never code. Uploaded Python is not executed. It is either:
  custom     fixed resources and quantities, plus each agent's private points
             (exactly 100 per agent, spread over the resources), or
  generated  the research generator (spec §1-§2): pool and valuations drawn from the
             instance seed, as in the controlled experiment.
Both set the round limit and the opening seat. The actions (OFFER / ACCEPT /
WALK_AWAY) and termination rules are the frozen protocol's (spec §3, §8.5) and are
not configurable. Points become per-unit values exactly as the research environment
defines them (points / quantity), so every utility and metric keeps its meaning.

Imports: JSON (the same shape as a saved environment) or a line-based text format:

    name: Field hospital
    rounds: 10
    first: A
    resources: food=20, water=12, medicine=6
    A: food=50, water=30, medicine=20
    B: food=10, water=30, medicine=60
"""
from __future__ import annotations

import json
import re

from src.environment.optimum import compute_optimal_welfare
from src.environment.resources import generate_resource_pool
from src.environment.valuations import TOTAL_POINTS, generate_valuation
from workbench.agents import WorkbenchError

NAME_RE = re.compile(r"^[a-z][a-z-]{0,19}$")
MIN_RES, MAX_RES, MAX_QTY = 2, 6, 1000
MIN_ROUNDS, MAX_ROUNDS = 2, 20
GEN_MIN, GEN_MAX = 2, 5  # the research generator's category names
RULES = {"actions": ["OFFER", "ACCEPT", "WALK_AWAY"], "agents": 2,
         "termination": ["ACCEPT of the opponent's standing offer → agreed",
                         "WALK_AWAY on any turn → walked_away",
                         "a valid OFFER on the final turn → timeout",
                         "malformed output, an infeasible allocation or an illegal ACCEPT → invalid_action"]}


def _int(v, lo, hi, what, where):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise WorkbenchError(f"{what} must be a whole number from {lo} to {hi}.", where)
    return v


def validate(doc) -> dict:
    """A clean environment definition, or WorkbenchError naming the field to fix."""
    if not isinstance(doc, dict):
        raise WorkbenchError("The environment must be a JSON object.")
    name = str(doc.get("name", "")).strip()
    if not 1 <= len(name) <= 60:
        raise WorkbenchError("Give the environment a name of 1 to 60 characters.", "name")
    description = str(doc.get("description") or "").strip()
    if len(description) > 500:
        raise WorkbenchError("Keep the description under 500 characters.", "description")
    rounds = _int(doc.get("max_rounds", 10), MIN_ROUNDS, MAX_ROUNDS, "The round limit", "max_rounds")
    first = doc.get("first_mover", "A")
    if first not in ("A", "B"):
        raise WorkbenchError("The opening seat must be A or B.", "first_mover")
    base = {"name": name, "description": description, "max_rounds": rounds, "first_mover": first}
    source = doc.get("source", "custom")
    if source == "generated":
        return {**base, "source": "generated",
                "categories": _int(doc.get("categories", 3), GEN_MIN, GEN_MAX, "The number of resources", "categories")}
    if source != "custom":
        raise WorkbenchError("The source must be 'custom' or 'generated'.", "source")

    res = doc.get("resources")
    if not isinstance(res, dict) or not MIN_RES <= len(res) <= MAX_RES:
        raise WorkbenchError(f"Define {MIN_RES} to {MAX_RES} resources.", "resources")
    resources = {}
    for raw, qty in res.items():
        n = str(raw).strip().lower()
        if not NAME_RE.match(n):
            raise WorkbenchError(f"Resource “{raw}”: use 1–20 lowercase letters or hyphens, starting with a letter.", f"resources.{raw}")
        if n in resources:
            raise WorkbenchError(f"The resource “{n}” is listed twice.", f"resources.{n}")
        resources[n] = _int(qty, 1, MAX_QTY, f"The quantity of “{n}”", f"resources.{n}")
    pts = doc.get("points")
    if not isinstance(pts, dict):
        raise WorkbenchError("Give both agents private points.", "points")
    points = {}
    for seat in "AB":
        p = pts.get(seat)
        if not isinstance(p, dict):
            raise WorkbenchError(f"Give Agent {seat} private points for every resource.", f"points.{seat}")
        p = {str(k).strip().lower(): v for k, v in p.items()}
        if set(p) != set(resources):
            missing, extra = sorted(set(resources) - set(p)), sorted(set(p) - set(resources))
            raise WorkbenchError(f"Agent {seat}'s points must cover exactly the resources"
                                 f"{f'; missing {missing}' if missing else ''}{f'; unknown {extra}' if extra else ''}.", f"points.{seat}")
        for c in resources:
            _int(p[c], 0, TOTAL_POINTS, f"Agent {seat}'s points for “{c}”", f"points.{seat}.{c}")
        if sum(p.values()) != TOTAL_POINTS:
            raise WorkbenchError(f"Agent {seat}'s points add up to {sum(p.values())}; they must add up to exactly {TOTAL_POINTS}.", f"points.{seat}")
        points[seat] = {c: p[c] for c in resources}
    return {**base, "source": "custom", "resources": resources, "points": points, **_meta(doc.get("meta"), resources)}


META_RE = re.compile(r"^[\w ./%-]{0,24}$")


def _meta(raw, resources: dict) -> dict:
    """Optional display labels per resource: a unit ("kg", "hours") and a group ("materials").
    Labels only: the engine and every metric see the same pool and points without them."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise WorkbenchError("Resource details must be an object.", "meta")
    out = {}
    for c, m in raw.items():
        c = str(c).strip().lower()
        if c not in resources:
            continue  # details for a resource that was removed are dropped, not an error
        if not isinstance(m, dict):
            raise WorkbenchError(f"Details for “{c}” must be an object.", f"meta.{c}")
        entry = {}
        for key, what in (("unit", "unit"), ("group", "category")):
            v = str(m.get(key) or "").strip()
            if not META_RE.match(v):
                raise WorkbenchError(f"The {what} of “{c}”: at most 24 letters, digits, spaces or . / % -.", f"meta.{c}.{key}")
            if v:
                entry[key] = v
        if entry:
            out[c] = entry
    return {"meta": out} if out else {}


def parse_text(text: str) -> dict:
    """The line-based format (see module docstring) as an unvalidated definition."""
    doc, seen = {"source": "custom", "points": {}}, set()
    for n, line in enumerate(str(text).splitlines(), start=1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if not sep:
            raise WorkbenchError(f"Line {n}: expected “key: value”, got “{line}”.", f"line {n}")
        if key in seen:
            raise WorkbenchError(f"Line {n}: “{key}” is given twice.", f"line {n}")
        seen.add(key)
        value = value.strip()
        if key in ("name", "description"):
            doc[key] = value
        elif key in ("rounds", "max_rounds"):
            if not value.isdigit():
                raise WorkbenchError(f"Line {n}: rounds must be a whole number.", f"line {n}")
            doc["max_rounds"] = int(value)
        elif key in ("first", "first_mover"):
            doc["first_mover"] = value.upper()
        elif key in ("resources", "a", "b"):
            pairs = {}
            for item in filter(None, (s.strip() for s in value.split(","))):
                k, eq, v = item.partition("=")
                if not eq or not v.strip().isdigit():
                    raise WorkbenchError(f"Line {n}: write each entry as name=whole number, got “{item}”.", f"line {n}")
                pairs[k.strip()] = int(v)
            if key == "resources":
                doc["resources"] = pairs
            else:
                doc["points"][key.upper()] = pairs
        else:
            raise WorkbenchError(f"Line {n}: unknown key “{key}” (use name, description, rounds, first, resources, A, B).", f"line {n}")
    return doc


def parse(fmt: str, content) -> dict:
    """Parse an import, then validate it."""
    if fmt == "json":
        try:
            doc = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError as exc:
            raise WorkbenchError(f"Not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.", f"line {exc.lineno}") from None
    elif fmt == "text":
        doc = parse_text(content)
    elif fmt == "form":
        doc = content
    else:
        raise WorkbenchError("Import JSON or the text format. Python scenario modules are not executed by this server.", "format")
    return validate(doc)


def instance(env: dict, seed: int) -> dict:
    """The concrete instance a negotiation runs on: pool, per-unit valuations and points."""
    if env["source"] == "generated":
        pool = generate_resource_pool(seed=seed, num_categories=env["categories"])
        val = {s: generate_valuation(seed, s, pool) for s in "AB"}
        points = {s: {c: round(val[s][c] * q, 2) for c, q in pool.items()} for s in "AB"}
    else:
        pool, points = dict(env["resources"]), env["points"]
        val = {s: {c: points[s][c] / q for c, q in pool.items()} for s in "AB"}
    alloc, welfare = compute_optimal_welfare(pool, val["A"], val["B"])
    return {"seed": seed, "pool": pool, "valuation": val, "points": points,
            "optimal_allocation": {"A": alloc.a_A, "B": alloc.a_B}, "optimal_welfare": welfare}


def preview(env: dict, seed: int = 0) -> dict:
    return {"environment": env, "instance": instance(env, seed), "rules": {**RULES, "max_rounds": env["max_rounds"]},
            "seed_dependent": env["source"] == "generated"}


BUILTIN = {d["id"]: {**validate(d), "id": d["id"], "builtin": True} for d in (
    {"id": "builtin-research", "name": "Research negotiation (generator)", "source": "generated", "categories": 3,
     "max_rounds": 10, "first_mover": "A",
     "description": "The controlled experiment's environment: three resources with quantities and both agents' private "
                    "valuations drawn from the instance seed (spec §1-§2). Each seed is a different instance."},
    {"id": "builtin-field-hospital", "name": "Field hospital supplies", "max_rounds": 10, "first_mover": "A",
     "description": "Two relief teams split scarce supplies. Team A needs food and water most; team B needs medicine.",
     "resources": {"food": 20, "water": 12, "medicine": 6},
     "points": {"A": {"food": 50, "water": 30, "medicine": 20}, "B": {"food": 10, "water": 30, "medicine": 60}},
     "meta": {"food": {"unit": "crates", "group": "provisions"}, "water": {"unit": "tanks", "group": "provisions"}, "medicine": {"unit": "kits", "group": "medical"}}},
    {"id": "builtin-shared-lab", "name": "Shared lab time", "max_rounds": 8, "first_mover": "B",
     "description": "Two groups divide instrument hours with nearly identical priorities: little room for trade.",
     "resources": {"microscope": 10, "sequencer": 10, "cluster": 10},
     "points": {"A": {"microscope": 40, "sequencer": 35, "cluster": 25}, "B": {"microscope": 40, "sequencer": 30, "cluster": 30}},
     "meta": {"microscope": {"unit": "hours", "group": "instruments"}, "sequencer": {"unit": "hours", "group": "instruments"}, "cluster": {"unit": "node-hours", "group": "computing"}}},
)}
