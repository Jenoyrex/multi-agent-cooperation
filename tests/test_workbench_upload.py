"""Uploaded Python agents: inspected by syntax tree, registered, never executed, never runnable."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from src.zero_cost import network_tripwire
from workbench import agents as A, environments as E, simulations as S, store
from workbench.agents import WorkbenchError
from workbench.inspect_agent import MAX_SOURCE_BYTES, inspect_source

TEMPLATE = Path(__file__).resolve().parents[1] / "dashboard" / "static" / "templates" / "my_custom_agent.py"
GOOD = TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "wb.db")
    with network_tripwire() as blocked:
        yield
    assert blocked == []


def test_the_starter_template_is_compatible():
    r = inspect_source("my_custom_agent.py", GOOD)
    assert r["status"] == "compatible" and r["compatible_classes"] == ["MyCustomAgent"] and r["executed"] is False
    assert not [i for i in r["issues"] if i["level"] == "error"]


def test_the_starter_template_really_negotiates_on_the_engine():
    """The template is our own trusted file: run it once against the built-in Priority agent to prove it works."""
    from src.environment.allocation import Allocation
    from src.negotiation.protocol import EvaluatorState, NegotiationSession
    spec = importlib.util.spec_from_file_location("template_agent", TEMPLATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = E.BUILTIN["builtin-field-hospital"]
    inst = E.instance(env, 0)
    state = EvaluatorState(instance_seed=0, resource_pool=inst["pool"], valuation_A=inst["valuation"]["A"], valuation_B=inst["valuation"]["B"],
                           optimal_allocation=Allocation(a_A=inst["optimal_allocation"]["A"], a_B=inst["optimal_allocation"]["B"]),
                           optimal_welfare=inst["optimal_welfare"], max_rounds=10, first_mover="A", first_mover_policy="explicit",
                           method="baseline_v1", model_A_name="template", model_B_name="priority", run_id="t", negotiation_index=0)
    record = asyncio.run(NegotiationSession(mod.MyCustomAgent(), A.PriorityAgent("p"), state).run())
    assert record.outcome in {"agreed", "walked_away", "timeout"}  # never invalid_action


def test_inspection_never_executes_the_file(tmp_path):
    marker = tmp_path / "ran.txt"
    src = f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(3)\n" + GOOD
    r = inspect_source("evil.py", src)
    assert not marker.exists()
    assert r["status"] == "needs-changes" and any("open()" in i["text"] for i in r["issues"])


@pytest.mark.parametrize("src,status,needle", [
    ("def broken(:\n", "invalid", "could not read"),
    ("x = 1\n", "invalid", "No class matches"),
    ("class A:\n    def generate_response(self, view):\n        return None\n", "invalid", "must be async"),
    ("class A:\n    async def generate_response(self):\n        return None\n", "invalid", "exactly (self, view)"),
    ("import requests\n" + GOOD, "needs-changes", "requests"),
    ("import subprocess\n" + GOOD, "needs-changes", "subprocess"),
    ("from anthropic import Anthropic\n" + GOOD, "needs-changes", "anthropic"),
    ("import numpy\n" + GOOD, "compatible", "not part of the standard set"),
], ids=["syntax", "no-class", "sync", "signature", "requests", "subprocess", "provider", "unknown-import"])
def test_inspection_reports(src, status, needle):
    r = inspect_source("agent.py", src)
    assert r["status"] == status
    assert any(needle in i["text"] for i in r["issues"]), r["issues"]


@pytest.mark.parametrize("filename,source,field", [
    ("agent.txt", GOOD, "upload.filename"), ("../x.py", GOOD, "upload.filename"), (None, GOOD, "upload.filename"),
    ("a.py", "   ", "upload.source"), ("a.py", 7, "upload.source"), ("a.py", "#" * (MAX_SOURCE_BYTES + 1), "upload.source"),
], ids=["txt", "traversal", "none", "blank", "not-text", "too-large"])
def test_unusable_uploads_are_refused(filename, source, field):
    with pytest.raises(WorkbenchError) as exc:
        inspect_source(filename, source)
    assert exc.value.field == field


def test_an_uploaded_agent_saves_but_can_never_run():
    from workbench import api
    doc = {"name": "Mine", "kind": "python_upload", "params": {"class_name": "MyCustomAgent"},
           "upload": {"filename": "my_custom_agent.py", "source": GOOD, "status": "compatible"}}
    saved, code = api.post_route("/api/workbench/agents")(doc)
    assert code == 201 and saved["upload"]["sha256"] and saved["upload"]["status"] == "compatible"
    listed = api.get("/api/workbench/agents")[0]
    mine = next(a for a in listed if a["id"] == saved["id"])
    assert mine["executable"] is False and "source" not in mine["upload"]
    assert api.get(f"/api/workbench/agents/{saved['id']}")[0]["upload"]["source"] == GOOD
    with pytest.raises(WorkbenchError) as exc:
        S.validate({"environment": "builtin-field-hospital", "agents": {"X": saved["id"], "Y": "builtin-priority"}, "conditions": ["baseline_v1"]})
    assert exc.value.field == "agents.X" and "not executed" in str(exc.value)
    with pytest.raises(WorkbenchError, match="not a compatible class"):
        A.validate({**doc, "params": {"class_name": "Nope"}})
    with pytest.raises(WorkbenchError) as exc:
        A.validate({**doc, "upload": None})
    assert exc.value.field == "upload"


def test_the_upload_routes_accept_a_file_larger_than_the_default_body_limit():
    from dashboard import server
    from tests.test_server import _request, _serve
    srv = _serve()
    port = srv.server_address[1]
    hdr = [("Host", f"127.0.0.1:{port}"), ("Content-Type", "application/json")]
    big = GOOD + "\n" + "# padding\n" * 2500  # ~25 KB of source: over the 16 KB default
    try:
        body = json.dumps({"filename": "my_custom_agent.py", "source": big}).encode()
        assert len(body) > server.MAX_BODY_BYTES
        status, _, raw = _request(port, "POST", "/api/workbench/uploads/inspect", hdr, body)
        assert status == 200 and json.loads(raw)["status"] == "compatible"
        assert _request(port, "POST", "/api/workbench/environments/preview", hdr, body)[0] == 413  # other routes keep 16 KB
    finally:
        srv.shutdown()
        srv.server_close()


def test_resource_units_and_categories_are_labels_only():
    base = {"name": "T", "resources": {"food": 20, "water": 12}, "points": {"A": {"food": 70, "water": 30}, "B": {"food": 20, "water": 80}}}
    env = E.validate({**base, "meta": {"food": {"unit": "kg", "group": "provisions"}, "gone": {"unit": "x"}, "water": {}}})
    assert env["meta"] == {"food": {"unit": "kg", "group": "provisions"}}
    assert E.instance(env, 0) == E.instance(E.validate(base), 0)  # the engine sees the same instance
    with pytest.raises(WorkbenchError) as exc:
        E.validate({**base, "meta": {"food": {"unit": "<script>"}}})
    assert exc.value.field == "meta.food.unit"
