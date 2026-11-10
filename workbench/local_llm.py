"""Free local LLMs through Ollama: an open model on this computer as a negotiating agent.

It gets what the Claude and GPT adapters get: the frozen system instructions and prompt, the same
structured-output schema, and the frozen parser, so a malformed answer is an invalid action and
results are comparable. Only the transport differs, and it is HTTPAgent's, loopback-only.
"""
from __future__ import annotations

import http.client
import json

from lab.adapters import HTTPAgent
from src.agents.base import AgentView
from src.agents.openai_agent import response_schema
from src.agents.prompting import build_prompt, system_instructions
from src.agents.schema import InvalidAgentOutputError, NegotiationAction
from src.experiments.config import APPROVED_SCIENTIFIC

OLLAMA_HOST, OLLAMA_PORT = "127.0.0.1", 11434
TEMPERATURE = APPROVED_SCIENTIFIC["OpenAIAgent"]["temperature"]  # the study's value, so local runs stay comparable
MAX_OUTPUT_TOKENS = APPROVED_SCIENTIFIC["OpenAIAgent"]["max_output_tokens"]


class OllamaAgent(HTTPAgent):
    def __init__(self, label: str, model: str, base_url: str, instructions_variant: str, timeout_s: float):
        super().__init__(base_url.rstrip("/") + "/api/chat", label, instructions_variant, timeout_s=timeout_s)
        self.name, self.model = f"ollama:{model}", model

    def _body(self, view: AgentView) -> bytes:
        return json.dumps({
            "model": self.model, "stream": False, "format": response_schema(view.resource_pool)["schema"],
            "options": {"temperature": TEMPERATURE, "num_predict": MAX_OUTPUT_TOKENS},
            "messages": [{"role": "system", "content": system_instructions(view, self.instructions_variant)},
                         {"role": "user", "content": build_prompt(view)}],
        }).encode()

    def _interpret(self, view: AgentView, data: bytes) -> NegotiationAction:
        try:
            reply = json.loads(data.decode("utf-8"))
            content, stop = reply["message"]["content"], reply.get("done_reason")
        except Exception as exc:  # noqa: BLE001 - untrusted input: every failure is malformed_output
            raise InvalidAgentOutputError(f"Ollama's reply could not be read ({type(exc).__name__})") from None
        if stop == "length":
            raise InvalidAgentOutputError("truncated output: done_reason=length")
        return super()._interpret(view, str(content).encode("utf-8"))


def installed_models() -> dict:
    """Which models Ollama has on this computer. Always this machine's default port, never a caller's URL."""
    conn = http.client.HTTPConnection(OLLAMA_HOST, OLLAMA_PORT, timeout=2)
    try:
        conn.request("GET", "/api/tags")
        res = conn.getresponse()
        names = [m["name"] for m in json.loads(res.read(1_000_000))["models"]] if res.status == 200 else None
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        names = None
    finally:
        conn.close()
    return {"running": names is not None, "models": sorted(names or [])}
