"""Workbench backend: agents, environments, simulations, analytics and exports,
offline (network tripwire) on a temporary database."""
import pytest

from src.zero_cost import network_tripwire
from workbench import agents as A, api, environments as E, simulations as S, store
from workbench.agents import WorkbenchError


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "wb.db")
    with network_tripwire() as blocked:
        yield
    assert blocked == []


TEXT = "name: T\nrounds: 6\nresources: food=20, water=12\nA: food=70, water=30\nB: food=20, water=80\n"


def test_text_and_json_imports_validate_and_preview():
    env = E.parse("text", TEXT)
    assert env["resources"] == {"food": 20, "water": 12} and env["max_rounds"] == 6
    assert E.parse("json", '{"name": "T", "max_rounds": 6, "resources": {"food": 20, "water": 12}, '
                           '"points": {"A": {"food": 70, "water": 30}, "B": {"food": 20, "water": 80}}}') == env
    inst = E.preview(env)["instance"]
    assert inst["optimal_allocation"] == {"A": {"food": 20, "water": 0}, "B": {"food": 0, "water": 12}}


@pytest.mark.parametrize("fmt,content,field", [
    ("text", TEXT.replace("water=80", "water=70"), "points.B"),
    ("text", TEXT + "colour: red\n", "line 6"),
    ("json", "{bad", "line 1"),
    ("python", "import os", "format"),
    ("form", {"name": "T", "resources": {"Food!": 1, "water": 2}}, "resources.Food!"),
])
def test_import_errors_name_the_field(fmt, content, field):
    with pytest.raises(WorkbenchError) as exc:
        E.parse(fmt, content)
    assert exc.value.field == field


def test_agents_validate_and_providers_stay_locked():
    with pytest.raises(WorkbenchError, match="minimum claim"):
        A.validate({"name": "x", "kind": "concession", "params": {"opening_ask": 0.5, "min_ask": 0.6}})
    with pytest.raises(WorkbenchError, match="this machine only"):
        A.validate({"name": "x", "kind": "http_local", "params": {"url": "http://example.org/turn"}})
    for kind in ("claude", "openai", "gemini"):
        assert not A.KINDS[kind].executable
    with pytest.raises(WorkbenchError, match="cannot run"):
        S.validate({"environment": "builtin-research", "agents": {"X": "builtin-claude", "Y": "builtin-concession"},
                    "conditions": ["baseline_v1"]})


def test_simulation_runs_persists_analyses_and_exports():
    saved, status = api.post_route("/api/workbench/environments")({**E.parse("text", TEXT), "name": "Mine"})
    assert status == 201
    sim = S.start({"environment": saved["id"], "agents": {"X": "builtin-priority", "Y": "builtin-hardliner"},
                   "conditions": ["baseline_v1", "structured_v1"], "repetitions": 2}, background=False)
    doc, _ = api.get(f"/api/workbench/simulations/{sim.id}")
    assert doc["status"] == "completed" and len(doc["trials"]) == 16
    assert doc["config"]["data_kind"] == "simulated" and doc["config"]["label"].startswith("SIMULATED")
    a = doc["analytics"]
    assert a["n_completed"] == 16 and sum(a["overall"]["outcomes"].values()) == 16
    b, s = a["by_condition"]["baseline_v1"], a["by_condition"]["structured_v1"]
    assert (b["rwe"], b["agreement_rate"]) == (s["rwe"], s["agreement_rate"])  # rule agents ignore instructions
    for t in doc["trials"]:  # RWE/EW score failures as 0
        if t["outcome"] != "agreed":
            assert t["rwe"] == 0 and t["ew"] == 0
    for name in ("config.json", "negotiations.csv", "summary.csv", "report.md", "transcripts.json"):
        content = api.get(f"/api/workbench/simulations/{sim.id}/export/{name}")[0]["content"]
        assert "SIMULATED" in content, name


def test_builtins_are_read_only_and_deletes_work():
    with pytest.raises(WorkbenchError, match="read-only"):
        api.post_route("/api/workbench/agents")({"id": "builtin-priority", "name": "x", "kind": "priority"})
    doc, _ = api.post_route("/api/workbench/agents")({"name": "Mine", "kind": "concession"})
    assert api.post_route(f"/api/workbench/agents/{doc['id']}/delete")({})[1] == 200
    assert api.get(f"/api/workbench/agents/{doc['id']}")[1] == 404


# ------------------------------------------------------------ through the real server guards
def test_workbench_routes_sit_behind_the_server_guards():
    import json
    from tests.test_server import _request, _serve
    srv = _serve()
    port = srv.server_address[1]
    host = [("Host", f"127.0.0.1:{port}")]
    body = json.dumps({"format": "text", "content": TEXT}).encode()
    try:
        status, _, raw = _request(port, "GET", "/api/workbench/catalog", host)
        assert status == 200 and json.loads(raw)["zero_cost"] is True
        assert _request(port, "GET", "/api/workbench/catalog", [("Host", "evil.example")])[0] == 403
        ok = _request(port, "POST", "/api/workbench/environments/preview", host + [("Content-Type", "application/json")], body)
        assert ok[0] == 200 and json.loads(ok[2])["instance"]["optimal_welfare"] == 150
        cross = _request(port, "POST", "/api/workbench/agents", host + [("Origin", "http://evil.example"),
                                                                        ("Content-Type", "application/json")], b'{"name":"x","kind":"concession"}')
        assert cross[0] == 403 and store.all_("agents") == []
        bad = _request(port, "POST", "/api/workbench/agents", host + [("Content-Type", "application/json")], b'{"name":"","kind":"concession"}')
        assert bad[0] == 400 and json.loads(bad[2])["field"] == "name"
        locked = _request(port, "POST", "/api/workbench/simulations", host + [("Content-Type", "application/json")],
                          json.dumps({"environment": "builtin-research", "agents": {"X": "builtin-gpt", "Y": "builtin-priority"},
                                      "conditions": ["baseline_v1"]}).encode())
        assert locked[0] == 400 and json.loads(locked[2])["field"] == "agents.X"
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_workbench_is_read_only_in_the_public_demo(monkeypatch):
    from dashboard import demo
    monkeypatch.setenv(demo.ENV, "1")
    with pytest.raises(WorkbenchError) as exc:
        api.post_route("/api/workbench/agents")({"name": "x", "kind": "concession"})
    assert exc.value.status == 403 and store.all_("agents") == []


def test_http_agents_are_known_to_read_instructions():
    """They receive the condition's instructions in every request, so no statement may claim otherwise."""
    assert A.KINDS["http_local"].reads_instructions
    assert not A.KINDS["concession"].reads_instructions and not A.KINDS["priority"].reads_instructions


def test_csv_exports_neutralise_formula_prefixes():
    from workbench.export import _csv
    assert _csv(["name"], [["=cmd()"], ["plain"]]) == "name\n'=cmd()\nplain\n"


def test_a_non_string_id_is_a_clean_400():
    with pytest.raises(WorkbenchError) as exc:
        api.post_route("/api/workbench/agents")({"id": ["x"], "name": "x", "kind": "concession"})
    assert exc.value.field == "id" and exc.value.status == 400
