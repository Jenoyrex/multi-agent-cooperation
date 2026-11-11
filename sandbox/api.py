"""The /api/models/* routes: connections, keys, model lists and the connection test.

They do not exist in the public demo, and dashboard/server.py answers them only for this computer, same-origin.
A key arrives once and never goes back: every response carries the masked form at most.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from sandbox import keys, policy, providers
from workbench import store
from workbench.agents import WorkbenchError

CONN_ID = r"[0-9a-f]{12}"


def get(path: str) -> tuple[dict, int]:
    policy.require_local()
    if path == "/api/models/connections":
        return {"connections": providers.listing(), "structured_modes": list(providers.STRUCTURED),
                "custom_help": providers.CUSTOM_HELP, "subscription_note": providers.SUBSCRIPTION_NOTE,
                "env_file": str(keys.env_file())}, 200
    return {"error": "not found"}, 404


def _obj(req) -> dict:
    if not isinstance(req, dict):
        raise WorkbenchError("Expected a JSON object.")
    return req


def _save_connection(req):
    req = _obj(req)
    if "key" in req or "api_key" in req:
        raise WorkbenchError("Add the key in the key field; a connection never stores one.", "key")
    clean = providers.validate(req)
    id_ = req.get("id")
    if id_ is not None and (not isinstance(id_, str) or store.get("connections", id_) is None):
        raise WorkbenchError("No saved connection has that id.", "id", 404)
    doc = store.put("connections", {**clean, "id": id_ or None})
    return providers.public(doc), 201


def _delete_connection(cid: str):
    def handler(req):
        if not store.delete("connections", cid):
            raise WorkbenchError("Nothing to delete: no saved connection has that id.", status=404)
        keys.remove(cid)
        return {"deleted": cid}, 200
    return handler


def _set_key(req):
    req = _obj(req)
    conn = providers.require(req.get("connection"), "connection")
    key = req.get("key")
    if not isinstance(key, str) or not 8 <= len(key.strip()) <= keys.MAX_KEY_CHARS or re.search(r"[\s\"'\\]", key.strip()) or not key.strip().isprintable():
        raise WorkbenchError("That does not look like an API key.", "key")
    if not providers.needs_key(conn):
        raise WorkbenchError(f"{conn['name']} runs on this computer and needs no key.", "connection")
    save = req.get("save", "session")
    if save not in ("session", "env_file"):
        raise WorkbenchError("Choose where to keep the key: this session only, or your .env file.", "save")
    if save == "env_file":
        try:
            keys.save_to_env(conn["id"], key.strip())
        except keys.KeyFileError as exc:
            raise WorkbenchError(str(exc), "save") from None
    else:
        keys.set_key(conn["id"], key.strip())
    return providers.public(conn), 200


def _remove_key(req):
    req = _obj(req)
    conn = providers.require(req.get("connection"), "connection")
    keys.remove(conn["id"], from_env_file=req.get("from_env_file") is True)
    return providers.public(conn), 200


def _forget_all(req):
    keys.forget_session()
    return {"connections": providers.listing()}, 200


def post_route(path: str) -> Optional[Callable]:
    fixed = {"/api/models/connections": _save_connection, "/api/models/keys": _set_key, "/api/models/keys/delete": _remove_key,
             "/api/models/keys/forget": _forget_all,
             "/api/models/list": lambda req: (providers.list_models(_obj(req).get("connection")), 200),
             "/api/models/test": lambda req: (providers.test_connection(_obj(req).get("connection"), req.get("model")), 200)}
    if path in fixed:
        return fixed[path]
    if m := re.fullmatch(rf"/api/models/connections/({CONN_ID})/delete", path):
        return _delete_connection(m[1])
    return None
