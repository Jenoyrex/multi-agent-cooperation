"""Agent library: the adapter interface between saved agent definitions and the engine.

An agent *kind* is an adapter. It declares its parameters, validates a saved
definition, and builds an `src.agents.base.Agent` (the only interface the engine
knows: `async generate_response(view) -> NegotiationAction`). Adding a provider or a
new Python agent means adding a kind here; the negotiation engine never changes.

Execution follows the zero-cost policy. Only kinds with `executable=True` can run:
rule-based agents, built-in Python agents, and HTTP or Ollama agents on this machine (loopback).
Provider kinds can be saved and configured, but stay locked. src/zero_cost.py also
refuses any real provider client underneath.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

from src.agents.base import Agent, AgentView
from src.agents.mock_agent import MockAgent
from src.agents.schema import NegotiationAction
from src.environment.allocation import compute_utility
from src.experiments.config import APPROVED_SCIENTIFIC
from src.zero_cost import LOOPBACK


class WorkbenchError(ValueError):
    """A request the workbench refuses; `field` names what to correct."""

    def __init__(self, message: str, field: Optional[str] = None, status: int = 400):
        super().__init__(message)
        self.field, self.status = field, status

    def payload(self) -> dict:
        return {"error": str(self), "field": self.field}


ACTIONS = ("OFFER", "ACCEPT", "WALK_AWAY")  # the frozen protocol's action format


# --------------------------------------------------------------- built-in Python agent
class PriorityAgent(Agent):
    """Deterministic, valuation-aware rule agent: a built-in Python agent.

    Claims `high_claim` of the resources worth most to it (the top half by its own
    points) and `low_claim` of the rest. It lowers its claim on its priorities by `step`
    after each of its own offers, down to `floor`. It accepts an opponent's standing
    offer worth at least `accept_threshold` of its maximum, and on the final turn
    accepts any standing offer worth more than nothing (an offer there could only
    time out); otherwise it walks away at the deadline."""

    def __init__(self, name: str, high_claim: float = 0.9, low_claim: float = 0.2, step: float = 0.05,
                 floor: float = 0.6, accept_threshold: float = 0.5):
        self.name, self.config = name, None
        self.high_claim, self.low_claim, self.step = high_claim, low_claim, step
        self.floor, self.accept_threshold = floor, accept_threshold

    @staticmethod
    def priorities(view: AgentView) -> list[str]:
        """Resources ordered by the points they are worth to this agent (ties by name)."""
        return sorted(view.resource_pool, key=lambda c: (-view.own_valuation[c] * view.resource_pool[c], c))

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        last = view.transcript_so_far[-1] if view.transcript_so_far else None
        if last and last.actor != view.role and last.action.action_type == "OFFER":
            mine = compute_utility(last.action.allocation[view.role], view.own_valuation)
            final = view.round_number == view.max_rounds
            if mine / 100 >= self.accept_threshold or (final and mine > 0):
                return NegotiationAction(action_type="ACCEPT", message=f"Accepting: worth {mine:.0f} of my 100 points.")
        if view.round_number == view.max_rounds:
            return NegotiationAction(action_type="WALK_AWAY", message="Deadline reached without an acceptable offer.")
        ranked = self.priorities(view)
        top = set(ranked[: math.ceil(len(ranked) / 2)])
        own_offers = sum(t.actor == view.role for t in view.transcript_so_far)
        high = max(self.floor, self.high_claim - self.step * own_offers)
        other = "B" if view.role == "A" else "A"
        mine = {c: max(0, min(q, round(q * (high if c in top else self.low_claim)))) for c, q in view.resource_pool.items()}
        return NegotiationAction(action_type="OFFER", allocation={view.role: mine, other: {c: q - mine[c] for c, q in view.resource_pool.items()}},
                                 message=f"I prioritise {', '.join(r for r in ranked if r in top)}.")


# ------------------------------------------------------------------- kinds
@dataclass(frozen=True)
class Param:
    name: str
    label: str
    type: str                 # "number" | "text" | "choice"
    default: object
    help: str
    min: Optional[float] = None
    max: Optional[float] = None
    choices: tuple = ()
    max_len: int = 200
    optional: bool = False


@dataclass(frozen=True)
class Kind:
    id: str
    label: str
    category: str             # mock | python | http | llm | upload | provider
    summary: str
    params: tuple
    executable: bool
    reason: str = ""          # why it cannot run, when it cannot
    reads_instructions: bool = False
    deterministic: bool = True
    cost: str = "free"
    build: Optional[Callable[[str, dict, str], Agent]] = field(default=None, compare=False)
    check: Optional[Callable[[dict], None]] = field(default=None, compare=False)

    def public(self) -> dict:
        return {"id": self.id, "label": self.label, "category": self.category, "summary": self.summary,
                "executable": self.executable, "reason": self.reason, "reads_instructions": self.reads_instructions,
                "deterministic": self.deterministic, "cost": self.cost, "actions": list(ACTIONS),
                "params": [{k: v for k, v in vars(p).items() if v not in (None, ())} for p in self.params]}


def _share(name, label, default, help, lo=0.0, hi=1.0):
    return Param(name, label, "number", default, help, lo, hi)


def _concession_check(p: dict) -> None:
    if p["min_ask"] > p["opening_ask"]:
        raise WorkbenchError("The minimum claim cannot exceed the opening claim.", "params.min_ask")


def _priority_check(p: dict) -> None:
    if p["floor"] > p["high_claim"]:
        raise WorkbenchError("The floor cannot exceed the opening claim on priorities.", "params.floor")


def _http_check(p: dict) -> None:
    u = urlparse(p["url"])
    if u.scheme != "http" or not u.hostname:
        raise WorkbenchError("Use an http:// URL on this machine, such as http://127.0.0.1:8001/turn.", "params.url")
    if u.hostname not in LOOPBACK:
        raise WorkbenchError("Zero-cost mode runs HTTP agents on this machine only (127.0.0.1, ::1 or localhost).", "params.url")


def _http_build(name: str, p: dict, variant: str) -> Agent:
    from lab.adapters import HTTPAgent  # local import: the Lab is only needed for this kind
    from sandbox import policy
    policy.require_local("Calling an HTTP agent")
    return HTTPAgent(p["url"], name, variant, timeout_s=p["timeout_s"])


def _llm_build(name: str, p: dict, variant: str) -> Agent:
    from workbench.local_llm import OllamaAgent  # local import: only this kind needs it
    return OllamaAgent(name, p["model"], p["url"], variant, p["timeout_s"])


def _remote_check(p: dict) -> None:
    from sandbox import policy
    u = urlparse(p["url"])
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise WorkbenchError("Use a full http:// or https:// URL, without credentials or a query in it (the URL is shown in reports and exports).", "params.url")
    if u.hostname in LOOPBACK:
        raise WorkbenchError("That address is on this computer. Use a Local HTTP agent for it.", "params.url")
    policy.require_local("A remote HTTP agent")


def _remote_build(name: str, p: dict, variant: str) -> Agent:
    from lab.adapters import HTTPAgent
    from sandbox import policy
    policy.require_local("Calling a remote HTTP agent")
    return HTTPAgent(p["url"], name, variant, timeout_s=p["timeout_s"], allow_remote=True)


def _prompt_check(p: dict) -> None:
    from sandbox import policy, providers
    policy.require_local("A prompt agent")
    providers.require(p["connection"])


def _prompt_build(name: str, p: dict, variant: str) -> Agent:
    from sandbox.model_agent import build
    return build(name, p, variant)


LOCKED = ("Locked by zero-cost mode: real provider calls cost money, so this project never makes them. "
          "The definition can be saved and reviewed; it cannot run.")
_model = lambda cls: Param("model", "Model", "choice", APPROVED_SCIENTIFIC[cls]["model"],  # noqa: E731
                           "The approved, pinned model snapshot (spec §8.15).",
                           choices=(APPROVED_SCIENTIFIC[cls]["model"],))

KINDS = {k.id: k for k in (
    Kind("concession", "Concession rule agent", "mock",
         "Claims the same share of every resource, concedes a fixed step each round, and accepts any offer above "
         "its threshold. Ignores its valuations when claiming.",
         (_share("opening_ask", "Opening claim", 0.75, "Share of each resource claimed in the first offer.", 0.05, 1.0),
          _share("min_ask", "Minimum claim", 0.35, "The claim never drops below this share."),
          _share("concession_step", "Concession per round", 0.08, "Share given up per round.", 0.0, 0.5),
          _share("accept_threshold", "Acceptance threshold", 0.55, "Accepts an offer worth at least this share of its 100 points.")),
         True, build=lambda name, p, v: MockAgent(name=name, **p), check=_concession_check),
    Kind("priority", "Priority agent (Python)", "python",
         "Built-in Python agent (workbench.agents.PriorityAgent). Claims most of the resources it values most and "
         "little of the rest, concedes on its priorities, and accepts at the deadline rather than let it pass.",
         (_share("high_claim", "Claim on priorities", 0.9, "Opening share claimed of the top half of resources by its own points.", 0.05, 1.0),
          _share("low_claim", "Claim on the rest", 0.2, "Share claimed of the resources it values less."),
          _share("step", "Concession per own offer", 0.05, "How much the claim on priorities drops after each of its offers.", 0.0, 0.5),
          _share("floor", "Floor on priorities", 0.6, "The claim on priorities never drops below this share."),
          _share("accept_threshold", "Acceptance threshold", 0.5, "Accepts an offer worth at least this share of its 100 points.")),
         True, build=lambda name, p, v: PriorityAgent(name, **p), check=_priority_check),
    Kind("http_local", "Local HTTP agent", "http",
         "Your own agent behind an HTTP endpoint on this machine, speaking the Agent Lab protocol (see lab/example_http_agent.py). "
         "Its behaviour is whatever that program does; the workbench does not verify it.",
         (Param("url", "Endpoint URL", "text", "http://127.0.0.1:8001/turn", "A loopback http:// URL. No token is stored."),
          Param("timeout_s", "Timeout per turn (s)", "number", 10, "Seconds to wait for each answer.", 1, 60)),
         True, reads_instructions=True, deterministic=False, build=_http_build, check=_http_check),  # each request carries the condition's instructions
    Kind("local_llm", "Local LLM (Ollama)", "llm",
         "An open model running on your own computer through Ollama. It receives the study's frozen instructions and prompt, "
         "and its answers are checked by the same parser as the Claude and GPT adapters.",
         (Param("model", "Model", "text", "llama3.2", "A model you have downloaded with Ollama."),
          Param("url", "Ollama address", "text", "http://127.0.0.1:11434", "Ollama on this computer. Change it only if you moved Ollama to another port."),
          Param("timeout_s", "Timeout per turn (s)", "number", 180, "Seconds to wait for each answer. Laptops without a graphics card need longer.", 5, 600)),
         True, reads_instructions=True, deterministic=False, build=_llm_build, check=_http_check),
    Kind("python_upload", "Uploaded Python agent", "upload",
         "Your own Python file, inspected by reading its syntax tree (workbench/inspect_agent.py). It is never imported "
         "or executed: this server has no isolated sandbox for untrusted code.",
         (Param("class_name", "Agent class", "text", "", "The class in your file that implements generate_response."),),
         False, "Inspected, not executed: this server has no isolated sandbox for uploaded Python. To run your agent, "
                "serve it yourself on this machine and add it as a Local HTTP agent (see lab/example_http_agent.py).",
         reads_instructions=False, deterministic=False),
    Kind("prompt", "Prompt agent (no coding)", "model",
         "Choose a model and write the agent's instructions. It sees what a study agent sees (the public pool, its own values, the "
         "transcript), plus your instructions; never the other agent's values.",
         (Param("connection", "Model connection", "text", "ollama", "Where the model runs: a local server, or a provider with your own key."),
          Param("model", "Model", "text", "", "The model's name on that connection."),
          Param("persona", "Instructions / personality", "text", "", "Added to the agent's rules. Leave empty for none.", max_len=4000, optional=True),
          Param("temperature", "Temperature", "number", None, "Leave empty to use the provider's default (some models refuse other values).", 0, 2, optional=True),
          Param("max_output_tokens", "Longest reply (tokens)", "number", 400, "A reply cut off at this length counts as an invalid action.", 16, 8000),
          Param("timeout_s", "Timeout per turn (s)", "number", 60, "A turn that takes longer ends that negotiation as aborted, not as an outcome.", 1, 600)),
         True, reads_instructions=True, deterministic=False, cost="free locally; with an API key, you pay your provider",
         build=_prompt_build, check=_prompt_check),
    Kind("http_remote", "Remote HTTP agent", "http",
         "Your own agent behind an HTTP or HTTPS URL on another machine, speaking the Agent Lab protocol. Each turn sends that agent's own view "
         "to that server, so it asks for your consent before an experiment runs. No token is stored.",
         (Param("url", "Endpoint URL", "text", "https://example.org/turn", "A URL on another machine. Use a Local HTTP agent for this computer."),
          Param("timeout_s", "Timeout per turn (s)", "number", 20, "Seconds to wait for each answer.", 1, 60)),
         True, reads_instructions=True, deterministic=False, cost="whatever that server charges you", build=_remote_build, check=_remote_check),
    Kind("python_file", "Python file (runs on your computer)", "upload",
         "Your own .py file, run in its own process on this computer after you confirm. The process gets a clean environment with no API keys and a "
         "time limit per turn, but it runs with your permissions and could read your files. Only run files you trust.",
         (Param("class_name", "Agent class", "text", "", "The class in your file that implements generate_response."),
          Param("timeout_s", "Timeout per turn (s)", "number", 10, "The process is stopped if a turn takes longer.", 1, 120)),
         True, reads_instructions=False, deterministic=False),
    Kind("claude", "Claude (Anthropic)", "provider", "Provider adapter: src.agents.claude_agent.ClaudeAgent with the approved runtime.",
         (_model("ClaudeAgent"),), False, LOCKED, reads_instructions=True, deterministic=False, cost="paid"),
    Kind("openai", "GPT (OpenAI)", "provider", "Provider adapter: src.agents.openai_agent.OpenAIAgent with the approved runtime.",
         (_model("OpenAIAgent"),), False, LOCKED, reads_instructions=True, deterministic=False, cost="paid"),
    Kind("gemini", "Gemini (Google)", "provider", "No adapter exists yet. Saving records a configuration only.",
         (Param("model", "Model id", "text", "gemini-model-id", "Recorded as given; nothing validates it against Google."),),
         False, "Not implemented: there is no Gemini adapter, and zero-cost mode would lock it anyway.",
         reads_instructions=True, deterministic=False, cost="paid"),
)}

MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
SERVER_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")  # names a server reports, such as org/model:tag
LOCAL_ONLY = frozenset({"http_local", "http_remote", "prompt", "python_file", "local_llm"})  # never in the public demo
UPLOAD_KINDS = frozenset({"python_upload", "python_file"})
NAME_MAX, NOTES_MAX = 60, 500


def validate(doc) -> dict:
    """A clean agent definition {name, kind, params, notes}, or WorkbenchError."""
    if not isinstance(doc, dict):
        raise WorkbenchError("The agent must be a JSON object.")
    name = str(doc.get("name", "")).strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise WorkbenchError(f"Give the agent a name of 1 to {NAME_MAX} characters.", "name")
    kind = KINDS.get(doc.get("kind")) if isinstance(doc.get("kind"), str) else None
    if kind is None:
        raise WorkbenchError(f"Choose an agent type: {', '.join(KINDS)}.", "kind")
    notes = str(doc.get("notes") or "").strip()
    if len(notes) > NOTES_MAX:
        raise WorkbenchError(f"Keep the role description under {NOTES_MAX} characters.", "notes")
    raw = doc.get("params") or {}
    if not isinstance(raw, dict):
        raise WorkbenchError("Parameters must be an object.", "params")
    unknown = sorted(set(raw) - {p.name for p in kind.params})
    if unknown:
        raise WorkbenchError(f"Unknown parameter(s) for {kind.label}: {', '.join(unknown)}.", f"params.{unknown[0]}")
    params = {}
    for p in kind.params:
        v = raw.get(p.name, p.default)
        where = f"params.{p.name}"
        if p.optional and v in (None, ""):
            params[p.name] = None if p.type == "number" else ""
            continue
        if p.type == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not p.min <= v <= p.max:
                raise WorkbenchError(f"{p.label} must be a number from {p.min:g} to {p.max:g}.", where)
            v = float(v)
        elif p.type == "choice":
            if v not in p.choices:
                raise WorkbenchError(f"{p.label} must be one of: {', '.join(p.choices)}.", where)
        else:
            v = str(v).strip()
            if p.name == "model" and kind.id == "prompt":
                if not SERVER_MODEL_ID.match(v):
                    raise WorkbenchError(f"{p.label}: choose a model name, using letters, digits and . _ : / @ + - (at most 128).", where)
            elif p.name == "model" and not MODEL_ID.match(v):
                raise WorkbenchError(f"{p.label}: use letters, digits, '.', '_', ':' or '-' (at most 64).", where)
            if len(v) > p.max_len or (not v and not p.optional):
                raise WorkbenchError(f"{p.label} is required (at most {p.max_len} characters).", where)
        params[p.name] = v
    if kind.check:
        kind.check(params)
    clean = {"name": name, "kind": kind.id, "params": params, "notes": notes}
    if kind.id == "python_file":  # saved for running later, so only this computer may add one
        from sandbox import policy
        policy.require_local("Adding a Python file to run")
    if kind.id in UPLOAD_KINDS:  # the server re-inspects the file; a client-side report is never trusted
        from workbench.inspect_agent import inspect_source
        up = doc.get("upload")
        if not isinstance(up, dict):
            raise WorkbenchError("Upload the agent's .py file.", "upload")
        report = inspect_source(up.get("filename"), up.get("source"))
        if params["class_name"] not in report["compatible_classes"]:
            found = ", ".join(report["compatible_classes"]) or "none"
            raise WorkbenchError(f"“{params['class_name']}” is not a compatible class in this file (compatible: {found}).", "params.class_name")
        clean["upload"] = {"source": up["source"], **{k: report[k] for k in ("filename", "sha256", "bytes", "lines", "status", "issues", "classes", "imports")}}
    return clean


def build(defn: dict, display_name: str, variant: str) -> Agent:
    """The engine-facing Agent for a saved definition, or WorkbenchError if it may not run."""
    kind = KINDS[defn["kind"]]
    if not kind.executable:
        raise WorkbenchError(f"{defn['name']} ({kind.label}) cannot run: {kind.reason}", "agents")
    if kind.id == "python_file":
        from sandbox.python_agent import PythonFileAgent
        return PythonFileAgent(display_name, defn["upload"]["source"], defn["params"]["class_name"], defn["params"]["timeout_s"])
    return kind.build(display_name, defn["params"], variant)


BUILTIN = {d["id"]: d for d in (
    {"id": "builtin-concession", "name": "Steady conceder", "kind": "concession", "builtin": True,
     "notes": "Asks for a lot, then gives ground a little each turn.",
     "params": {"opening_ask": 0.75, "min_ask": 0.35, "concession_step": 0.08, "accept_threshold": 0.55}},
    {"id": "builtin-hardliner", "name": "Hardliner", "kind": "concession", "builtin": True,
     "notes": "Opens high, concedes slowly and accepts only rich offers.",
     "params": {"opening_ask": 0.9, "min_ask": 0.6, "concession_step": 0.03, "accept_threshold": 0.7}},
    {"id": "builtin-priority", "name": "Priority trader", "kind": "priority", "builtin": True,
     "notes": "Trades low-value resources for high-value ones.",
     "params": {"high_claim": 0.9, "low_claim": 0.2, "step": 0.05, "floor": 0.6, "accept_threshold": 0.5}},
    {"id": "builtin-claude", "name": "Claude Sonnet 4.6", "kind": "claude", "builtin": True,
     "notes": "The research configuration. Locked in zero-cost mode.", "params": {"model": "claude-sonnet-4-6"}},
    {"id": "builtin-gpt", "name": "GPT-4.1", "kind": "openai", "builtin": True,
     "notes": "The research configuration. Locked in zero-cost mode.", "params": {"model": "gpt-4.1-2025-04-14"}},
)}
