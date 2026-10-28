"""HTTP routes of the workbench, dispatched from dashboard/server.py behind its
existing Host/Origin/Content-Type guards. GET returns (payload, status); POST routes
are looked up by path and take the parsed JSON body."""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from dashboard import demo
from src import zero_cost
from workbench import agents as A, environments as E, export, simulations as S, store
from workbench.agents import WorkbenchError
from workbench.analytics import analyse

ID = r"([0-9a-f]{12}|builtin-[a-z-]+)"


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


def _agent_view(d: dict, usage: dict) -> dict:
    kind = A.KINDS[d["kind"]]
    return {**d, "builtin": d.get("builtin", False), "kind_label": kind.label, "category": kind.category,
            "executable": kind.executable, "reason": kind.reason, "history": usage.get(d["id"], [])}


def _sim_header(s: dict) -> dict:
    done = [t for t in s["trials"] if t["status"] == "completed"]
    return {"id": s["id"], "name": s["config"]["name"], "status": s["status"], "created": s["created"],
            "finished": s.get("finished"), "planned": s["plan_size"], "done": len(s["trials"]),
            "data_kind": s["config"]["data_kind"], "environment": s["config"]["environment"]["name"],
            "agents": {k: d["name"] for k, d in s["config"]["agents"].items()},
            "agreement_rate": sum(t["outcome"] == "agreed" for t in done) / len(done) if done else None}


def catalog() -> dict:
    return {"kinds": [k.public() for k in A.KINDS.values()], "zero_cost": not zero_cost.paid_calls_allowed(),
            "limits": {"repetitions": S.MAX_REPETITIONS, "negotiations": S.MAX_NEGOTIATIONS,
                       "resources": [E.MIN_RES, E.MAX_RES], "quantity": E.MAX_QTY, "rounds": [E.MIN_ROUNDS, E.MAX_ROUNDS],
                       "generated_resources": [E.GEN_MIN, E.GEN_MAX]},
            "rules": E.RULES, "exports": export.FILES, "conditions": ["baseline_v1", "structured_v1"],
            "import_formats": {"json": True, "text": True, "yaml": False, "python": False},
            "read_only": demo.enabled()}


def get(path: str) -> tuple[dict, int]:
    usage = _usage() if path.startswith(("/api/workbench/agents", "/api/workbench/environments", "/api/workbench/overview")) else {}
    if path == "/api/workbench/catalog":
        return catalog(), 200
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
        return (_agent_view(d, usage), 200) if d else ({"error": "not found"}, 404)
    if m := re.fullmatch(rf"/api/workbench/environments/{ID}", path):
        d = S.resolve("environments", E.BUILTIN, m[1])
        return ({**E.preview(d, 0), "history": usage.get(d["id"], [])}, 200) if d else ({"error": "not found"}, 404)
    if m := re.fullmatch(r"/api/workbench/simulations/([0-9a-f]{12})", path):
        s = S.get(m[1])
        return ({**s, "analytics": analyse(s)}, 200) if s else ({"error": "not found"}, 404)
    if m := re.fullmatch(r"/api/workbench/simulations/([0-9a-f]{12})/export/([a-z]+\.(?:json|csv|md))", path):
        s = S.get(m[1])
        if not s or m[2] not in export.FILES:
            return {"error": "not found"}, 404
        return export.build(s, m[2]), 200
    return {"error": "not found"}, 404


# ------------------------------------------------------------------ POST
def _writable():
    if demo.enabled():
        raise WorkbenchError("The workbench is read-only in the public demo.", status=403)


def _save(table: str, builtin: dict, validator: Callable):
    def handler(req):
        _writable()
        if not isinstance(req, dict):
            raise WorkbenchError("Expected a JSON object.")
        clean = validator(req)
        id_ = req.get("id")
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
        _writable()
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


def _start(req):
    _writable()
    sim = S.start(req)
    return {"id": sim.id, "planned": len(sim.plan)}, 202


def post_route(path: str) -> Optional[Callable]:
    fixed = {"/api/workbench/agents": _save("agents", A.BUILTIN, A.validate),
             "/api/workbench/environments": _save("environments", E.BUILTIN, E.validate),
             "/api/workbench/environments/preview": _preview,
             "/api/workbench/simulations": _start}
    if path in fixed:
        return fixed[path]
    if m := re.fullmatch(rf"/api/workbench/(agents|environments)/{ID}/delete", path):
        return _delete(m[1], A.BUILTIN if m[1] == "agents" else E.BUILTIN, m[2])
    if m := re.fullmatch(r"/api/workbench/simulations/([0-9a-f]{12})/cancel", path):
        return lambda req: (S.cancel(m[1]), 200)
    return None
