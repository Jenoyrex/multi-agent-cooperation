"""HTTP routes of the workbench, dispatched from dashboard/server.py behind its
existing Host/Origin/Content-Type guards. GET returns (payload, status); POST routes
are looked up by path and take the parsed JSON body."""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from dashboard import demo
from sandbox import experiment, policy
from src import zero_cost
from workbench import agents as A, environments as E, export, simulations as S, store
from workbench.agents import WorkbenchError
from workbench import inspect_agent
from workbench import readable
from workbench.analytics import analyse

ID = r"([0-9a-f]{12}|builtin-[a-z-]+)"

# Public demo (PUBLIC_DEMO=1): one shared, presentation workspace. Visitors may create agents, resource sets and
# experiments and inspect uploads; nothing saved can be edited or deleted by a visitor, local HTTP agents are off
# (the server would call programs on its own host), experiments are small and share the demo's start limit, and the
# oldest entries are pruned so storage stays bounded. Everything is visible to every visitor.
DEMO_MAX_SAVED, DEMO_MAX_NEGOTIATIONS, DEMO_KEEP_SIMULATIONS = 40, 48, 30
DEMO_NO_HTTP = "Local HTTP agents and local LLMs are switched off in the public demo: the server would call a program on its own machine."


def _usage() -> dict:
    """Simulations each agent and environment took part in, newest first."""
    out: dict = {}
    for s in store.all_("simulations"):
        ref = {"id": s["id"], "name": s["config"]["name"], "status": s["status"], "created": s["created"]}
        for d in s["config"]["agents"].values():
            out.setdefault(d.get("id"), [])
            if ref not in out[d.get("id")]:
                out[d.get("id")].append(ref)
        out.setdefault(s["config"]["environment"].get("id"), []).append(ref)
    return out


def _agent_view(d: dict, usage: dict, full: bool = False) -> dict:
    kind = _kind_public(A.KINDS[d["kind"]])
    if "upload" in d and not full:  # lists carry the inspection report, not the uploaded source
        d = {**d, "upload": {k: v for k, v in d["upload"].items() if k != "source"}}
    return {**d, "builtin": d.get("builtin", False), "kind_label": kind["label"], "category": kind["category"],
            "executable": kind["executable"], "reason": kind["reason"], "history": usage.get(d["id"], [])}


def _sim_header(s: dict) -> dict:
    done = [t for t in s["trials"] if t["status"] == "completed"]
    return {"id": s["id"], "name": s["config"]["name"], "status": s["status"], "created": s["created"],
            "finished": s.get("finished"), "planned": s["plan_size"], "done": len(s["trials"]),
            "data_kind": s["config"]["data_kind"], "source_labels": s["config"].get("source_labels"),
            "environment": s["config"]["environment"]["name"], "sweep": len(s["config"].get("environments") or []) > 1,
            "stop_reason": s.get("stop_reason"),
            "agents": {k: d["name"] for k, d in s["config"]["agents"].items()},
            "agreement_rate": sum(t["outcome"] == "agreed" for t in done) / len(done) if done else None}


def _kind_public(k) -> dict:
    view = k.public()
    if demo.enabled() and k.id in A.LOCAL_ONLY:
        view.update(executable=False, reason=DEMO_NO_HTTP if k.id == "http_local" else policy.DEMO_OFF)
    return view


def catalog() -> dict:
    on = demo.enabled()
    return {"kinds": [_kind_public(k) for k in A.KINDS.values()], "zero_cost": not zero_cost.paid_calls_allowed(),
            "limits": {"repetitions": S.MAX_REPETITIONS, "negotiations": DEMO_MAX_NEGOTIATIONS if on else S.MAX_NEGOTIATIONS,
                       "resources": [E.MIN_RES, E.MAX_RES], "quantity": E.MAX_QTY, "rounds": [E.MIN_ROUNDS, E.MAX_ROUNDS],
                       "generated_resources": [E.GEN_MIN, E.GEN_MAX]},
            "rules": E.RULES, "exports": export.FILES, "conditions": ["baseline_v1", "structured_v1"],
            "import_formats": {"json": True, "text": True, "yaml": False, "python": False},
            "upload": {"max_bytes": inspect_agent.MAX_SOURCE_BYTES, "executes": False},
            "local_features": not on and policy.request_is_local(),
            "demo": on, "read_only": False}


def get(path: str) -> tuple[dict, int]:
    # Only the agent and environment pages show where each was used; the overview never did, but paid for it
    # (decoding every saved experiment twice).
    usage = _usage() if path.startswith(("/api/workbench/agents", "/api/workbench/environments")) else {}
    if path == "/api/workbench/catalog":
        return catalog(), 200
    if path == "/api/workbench/local-llm/models":
        if demo.enabled():  # the public server never looks for a model on its own machine
            return {"running": False, "models": []}, 200
        from workbench.local_llm import installed_models
        return installed_models(), 200
    if path == "/api/workbench/overview":
        sims = [_sim_header(s) for s in store.all_("simulations")]
        return {"agents": len(A.BUILTIN) + len(store.all_("agents")), "environments": len(E.BUILTIN) + len(store.all_("environments")),
                "simulations": sims[:8], "simulation_count": len(sims),
                "running": next((s for s in sims if s["status"] == "running"), None), "catalog": catalog()}, 200
    if path == "/api/workbench/agents":
        return [_agent_view(d, usage) for d in [*A.BUILTIN.values(), *store.all_("agents")]], 200
    if path == "/api/workbench/environments":
        return [{**d, "builtin": d.get("builtin", False), "history": usage.get(d["id"], [])}
                for d in [*E.BUILTIN.values(), *store.all_("environments")]], 200
    if path == "/api/workbench/simulations":
        return [_sim_header(s) for s in store.all_("simulations")], 200
    if m := re.fullmatch(rf"/api/workbench/agents/{ID}", path):
        d = S.resolve("agents", A.BUILTIN, m[1])
        return (_agent_view(d, usage, full=True), 200) if d else ({"error": "not found"}, 404)
    if m := re.fullmatch(rf"/api/workbench/environments/{ID}", path):
        d = S.resolve("environments", E.BUILTIN, m[1])
        return ({**E.preview(d, 0), "history": usage.get(d["id"], [])}, 200) if d else ({"error": "not found"}, 404)
    if m := re.fullmatch(r"/api/workbench/simulations/([0-9a-f]{12})", path):
        s = S.get(m[1])
        if not s:
            return {"error": "not found"}, 404
        a = analyse(s)
        return {**s, "analytics": a, "readable": readable.summary(s, a)}, 200
    if m := re.fullmatch(r"/api/workbench/simulations/([0-9a-f]{12})/export/([a-z]+\.(?:json|csv|md))", path):
        s = S.get(m[1])
        if not s or m[2] not in export.FILES:
            return {"error": "not found"}, 404
        return export.build(s, m[2]), 200
    return {"error": "not found"}, 404


# ------------------------------------------------------------------ POST
def _demo_create(table: str, req) -> None:
    """Public demo: new entries only, no local HTTP agents, and the oldest visitor entries pruned past the cap."""
    if not demo.enabled():
        return
    if isinstance(req, dict) and req.get("id") is not None:
        raise WorkbenchError("Saved entries cannot be changed in the public demo. Duplicate it to make your own version.", "id", 403)
    if table == "agents" and isinstance(req, dict) and req.get("kind") in A.LOCAL_ONLY:
        raise WorkbenchError(policy.DEMO_OFF, "kind", 403)
    for old in store.all_(table)[DEMO_MAX_SAVED - 1:]:  # newest first: keep room for this one
        store.delete(table, old["id"])


def _demo_start(req) -> None:
    """Public demo: small experiments, the shared start limit, and only the newest experiments kept."""
    if not demo.enabled():
        return
    cfg = S.validate(req)
    if any(d["kind"] in A.LOCAL_ONLY for d in cfg["agents"].values()):
        raise WorkbenchError(DEMO_NO_HTTP, "agents", 403)
    if cfg["total"] > DEMO_MAX_NEGOTIATIONS:
        raise WorkbenchError(f"This design has {cfg['total']} negotiations; the public demo runs at most {DEMO_MAX_NEGOTIATIONS}.", "repetitions")
    demo.take_start()
    done = [s for s in store.all_("simulations") if s["status"] != "running"]
    for old in done[DEMO_KEEP_SIMULATIONS - 1:]:
        store.delete("simulations", old["id"])


def _save(table: str, builtin: dict, validator: Callable):
    def handler(req):
        _demo_create(table, req)
        if not isinstance(req, dict):
            raise WorkbenchError("Expected a JSON object.")
        clean = validator(req)
        id_ = req.get("id")
        if id_ is not None and not isinstance(id_, str):
            raise WorkbenchError("The id must be a string.", "id")
        if id_ in builtin:
            raise WorkbenchError("Built-in entries are read-only. Duplicate it to make your own version.", "id")
        old = store.get(table, id_) if isinstance(id_, str) else None
        if id_ and old is None:
            raise WorkbenchError("No saved entry has that id.", "id", 404)
        doc = store.put(table, {**clean, "id": id_ or None, "created": old["created"] if old else time.time()})
        return doc, 200 if old else 201
    return handler


def _delete(table: str, builtin: dict, id_: str):
    def handler(req):
        if demo.enabled():
            raise WorkbenchError("Nothing can be deleted in the public demo; entries are shared by every visitor.", status=403)
        if id_ in builtin:
            raise WorkbenchError("Built-in entries cannot be deleted.", status=400)
        if not store.delete(table, id_):
            raise WorkbenchError("Nothing to delete: no saved entry has that id.", status=404)
        return {"deleted": id_}, 200
    return handler


def _preview(req):
    if not isinstance(req, dict):
        raise WorkbenchError("Expected a JSON object.")
    env = E.parse(req.get("format", "form"), req.get("content"))
    seed = req.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise WorkbenchError("The preview seed must be a whole number from 0.", "seed")
    return E.preview(env, seed), 200


def _inspect(req):
    if not isinstance(req, dict):
        raise WorkbenchError("Expected a JSON object.")
    return inspect_agent.inspect_source(req.get("filename"), req.get("source")), 200


def _estimate(req):
    """What a design could use, and which consents it needs. Sends nothing to any model."""
    cfg = S.validate(req)
    est, needed = experiment.estimate(cfg), experiment.required_consents(cfg)
    try:
        experiment.check_caps(cfg, est)
        problem = None
    except WorkbenchError as exc:
        problem = exc.payload()
    return {**est, "required": [{"id": c, "text": experiment.CONSENT_TEXT[c]} for c in needed], "token": experiment.consent_token(cfg) if needed else None,
            "caps_problem": problem, "source_labels": cfg["source_labels"], "data_kind": cfg["data_kind"], "label": cfg["label"], "total": cfg["total"]}, 200


def _start(req):
    _demo_start(req)
    sim = S.start(req)
    return {"id": sim.id, "planned": len(sim.plan)}, 202


def _cancel(id_: str):
    def handler(req):
        return S.cancel(id_), 200  # allowed in the public demo too, so a presenter can stop a long run
    return handler


def post_route(path: str) -> Optional[Callable]:
    fixed = {"/api/workbench/agents": _save("agents", A.BUILTIN, A.validate),
             "/api/workbench/environments": _save("environments", E.BUILTIN, E.validate),
             "/api/workbench/environments/preview": _preview,
             "/api/workbench/uploads/inspect": _inspect,
             "/api/workbench/simulations": _start,
             "/api/workbench/simulations/estimate": _estimate}
    if path in fixed:
        return fixed[path]
    if m := re.fullmatch(rf"/api/workbench/(agents|environments)/{ID}/delete", path):
        return _delete(m[1], A.BUILTIN if m[1] == "agents" else E.BUILTIN, m[2])
    if m := re.fullmatch(r"/api/workbench/simulations/([0-9a-f]{12})/cancel", path):
        return _cancel(m[1])
    return None
