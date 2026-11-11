"""Pages stay fast with 100+ saved experiments (a temp DB only), and old saved experiments still load."""
import time

import pytest

from workbench import api as WA, simulations as S, store

N = 120
LIMIT_S = 3.0  # per request, on a slow laptop; measured times are far lower


@pytest.fixture
def many(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "w.db")
    sim = S.start({"name": "seed", "environment": "builtin-field-hospital", "agents": {"X": "builtin-priority", "Y": "builtin-hardliner"},
                   "conditions": ["baseline_v1", "structured_v1"], "repetitions": 3}, background=False)
    doc = store.get("simulations", sim.id)
    for i in range(N - 1):
        store.put("simulations", {**doc, "id": None, "created": doc["created"] - i - 1, "config": {**doc["config"], "name": f"copy {i}"}})
    return doc


@pytest.mark.parametrize("path", ["/api/workbench/simulations", "/api/workbench/overview", "/api/workbench/agents", "/api/workbench/environments"])
def test_list_pages_answer_quickly_with_many_experiments(many, path):
    start = time.perf_counter()
    body, code = WA.get(path)
    took = time.perf_counter() - start
    assert code == 200 and took < LIMIT_S, f"{path} took {took:.2f} s"
    if path == "/api/workbench/simulations":
        assert len(body) == N


def test_one_experiment_with_analysis_answers_quickly(many):
    start = time.perf_counter()
    body, code = WA.get(f"/api/workbench/simulations/{many['id']}")
    assert code == 200 and body["analytics"] and time.perf_counter() - start < LIMIT_S


def test_an_old_experiment_without_new_fields_still_loads(many):
    """Experiments saved before this release have no usage_by_agent or spend."""
    old = {**many, "id": None}
    old.pop("spend", None)
    old["trials"] = [{k: v for k, v in t.items() if k != "usage_by_agent"} for t in many["trials"]]
    saved = store.put("simulations", old)
    body, code = WA.get(f"/api/workbench/simulations/{saved['id']}")
    assert code == 200 and body["analytics"]["sandbox"]["usage"]["per_agent"] == {}
