"""Local LLM agents: same prompts and parser as the study's adapters, this machine only, off in the public demo."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.agents.base import AgentView
from src.agents.prompting import build_prompt, system_instructions
from src.agents.schema import InvalidAgentOutputError, TransportError
from workbench import agents as A, api, local_llm
from workbench import simulations as S


def view():
    return AgentView(role="A", resource_pool={"x": 4, "y": 6}, own_valuation={"x": 10.0, "y": 10.0},
                     transcript_so_far=[], round_number=1, rounds_remaining=9, max_rounds=10)


OFFER = {"action_type": "OFFER", "allocation": {"x": 2, "y": 3}, "message": "Half each."}


@pytest.fixture
def ollama():
    """A stand-in for Ollama's HTTP API. `reply` is what /api/chat answers; `seen` is what it was sent."""
    state = {"reply": (200, {"message": {"content": json.dumps(OFFER)}, "done_reason": "stop"}), "seen": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["seen"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            status, body = state["reply"]
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()
    server.server_close()


def agent(url):
    defn = A.validate({"name": "Llama", "kind": "local_llm", "params": {"model": "llama3.2", "url": url, "timeout_s": 5}})
    return A.build(defn, "Llama", "baseline_v1")


def test_sends_the_frozen_prompts_and_parses_the_answer(ollama):
    action = asyncio.run(agent(ollama["url"]).generate_response(view()))
    assert action.action_type == "OFFER" and action.allocation["A"] == {"x": 2, "y": 3}
    sent = ollama["seen"][0]
    assert sent["model"] == "llama3.2" and sent["stream"] is False and "action_type" in sent["format"]["properties"]
    assert sent["messages"] == [{"role": "system", "content": system_instructions(view(), "baseline_v1")},
                                {"role": "user", "content": build_prompt(view())}]


@pytest.mark.parametrize("reply", [{"message": {"content": "I would like half."}}, {"unexpected": True},
                                   {"message": {"content": json.dumps(OFFER)}, "done_reason": "length"}])
def test_unusable_answers_are_invalid_actions(ollama, reply):
    ollama["reply"] = (200, reply)
    with pytest.raises(InvalidAgentOutputError):
        asyncio.run(agent(ollama["url"]).generate_response(view()))


def test_a_missing_model_is_infrastructure_not_a_move(ollama):
    ollama["reply"] = (404, {"error": "model not found"})
    with pytest.raises(TransportError):
        asyncio.run(agent(ollama["url"]).generate_response(view()))


def test_only_this_machine_is_allowed():
    with pytest.raises(A.WorkbenchError):
        A.validate({"name": "Remote", "kind": "local_llm", "params": {"url": "http://example.com:11434"}})


def test_a_run_with_a_local_llm_is_labelled_local_llm(monkeypatch):
    llm, real = A.validate({"name": "Llama", "kind": "local_llm", "params": {"model": "llama3.2"}}), S.resolve
    monkeypatch.setattr(S, "resolve", lambda table, builtin, ref: llm if ref == "llm" else real(table, builtin, ref))
    cfg = S.validate({"environment": "builtin-research", "agents": {"X": "llm", "Y": "builtin-priority"},
                      "conditions": ["baseline_v1"], "swap_seats": False, "swap_first_mover": False})
    assert cfg["data_kind"] == "local-llm" and cfg["label"].startswith("LOCAL LLM")


def test_ollama_not_running_is_reported_plainly(monkeypatch):
    monkeypatch.setattr(local_llm, "OLLAMA_PORT", 1)
    assert local_llm.installed_models() == {"running": False, "models": []}


def test_public_demo_cannot_save_or_list_local_llms(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "1")
    assert next(k for k in api.catalog()["kinds"] if k["id"] == "local_llm")["executable"] is False
    with pytest.raises(A.WorkbenchError):
        api._demo_create("agents", {"kind": "local_llm"})
    assert api.get("/api/workbench/local-llm/models") == ({"running": False, "models": []}, 200)
