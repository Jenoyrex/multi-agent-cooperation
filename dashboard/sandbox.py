"""Sandbox negotiations: interactive, exploratory runs of the frozen engine.

Kept apart from the controlled experiment in every way that matters:
- a run is built here and executed by NegotiationSession directly, never
  through run_batch or the pilot driver, so no research database is opened;
- each run is saved as JSON under sandbox_runs/ (never results/), tagged
  kind="sandbox", and is never read by the research views;
- nothing about the engine changes: the same protocol, the approved model
  settings (APPROVED_SCIENTIFIC / APPROVED_OPERATIONAL), the approved round
  limit and the two instruction variants. The visitor chooses only the
  scenario: resources, private points, strategy, who sits where, who opens.

Private points become per-unit valuations exactly as the research
environment defines them (points / quantity, 100 points per agent), so
every utility and metric keeps its meaning.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from dashboard import data as D
from src.agents.base import Agent
from src.agents.claude_agent import ClaudeAgent
from src.agents.generation import GenerationConfig
from src.agents.mock_agent import MockAgent
from src.agents.openai_agent import OpenAIAgent
from src.agents.prompting import INSTRUCTION_VARIANTS
from src.environment.optimum import compute_optimal_welfare
from src.experiments.config import APPROVED_MAX_ROUNDS, APPROVED_OPERATIONAL, APPROVED_PRICES, APPROVED_SCIENTIFIC
from src.negotiation.protocol import EvaluatorState, NegotiationAborted, NegotiationSession
from src.negotiation.usage import Budget
from src.storage.db import _turn_to_dict

ROOT = Path(__file__).resolve().parents[1]
SANDBOX_DIR = ROOT / "sandbox_runs"
load_dotenv(ROOT / ".env")  # API keys stay server-side; the browser never sees them

MODELS = {
    "claude": {"label": "Claude Sonnet 4.6", "provider": "Anthropic", "cls": ClaudeAgent, "key": "ANTHROPIC_API_KEY"},
    "openai": {"label": "GPT-4.1", "provider": "OpenAI", "cls": OpenAIAgent, "key": "OPENAI_API_KEY"},
}
SCRIPTED = "scripted"
MAX_COST_USD = 1.0      # hard cap per sandbox run, enforced by the engine's Budget
SCRIPTED_PACE_S = 0.7   # scripted agents answer instantly; pace turns so they can be followed
MIN_CATS, MAX_CATS, MAX_QTY, POINTS = 2, 5, 50, 100
NAME_RE = re.compile(r"^[a-z][a-z-]{0,19}$")


class SandboxError(ValueError):
    """A request the sandbox refuses, with a message fit for the visitor."""


def info() -> dict:
    """What the Try-it flow may offer. Key presence only, never key values."""
    return {
        "models": [{"id": k, "label": m["label"], "provider": m["provider"],
                    "model": APPROVED_SCIENTIFIC[m["cls"].__name__]["model"],
                    "ready": bool(os.environ.get(m["key"], "").strip()), "key_name": m["key"]}
                   for k, m in MODELS.items()],
        "max_rounds": APPROVED_MAX_ROUNDS, "max_cost_usd": MAX_COST_USD, "points": POINTS,
        "limits": {"min_resources": MIN_CATS, "max_resources": MAX_CATS, "max_quantity": MAX_QTY},
        "strategies": list(INSTRUCTION_VARIANTS),
        "busy": _active is not None,
    }


def validate(req) -> dict:
    """Normalize a Try-it request or raise SandboxError."""
    if not isinstance(req, dict):
        raise SandboxError("The request must be a JSON object.")
    agents = req.get("agents") or {}
    a, b = agents.get("A"), agents.get("B")
    choices = set(MODELS) | {SCRIPTED}
    if a not in choices or b not in choices:
        raise SandboxError("Choose a supported agent for both seats.")
    if (a == SCRIPTED) != (b == SCRIPTED):
        raise SandboxError("Scripted test agents run as a pair: use them in both seats, or AI models in both.")

    pool = req.get("pool")
    if not isinstance(pool, dict) or not MIN_CATS <= len(pool) <= MAX_CATS:
        raise SandboxError(f"Use between {MIN_CATS} and {MAX_CATS} resources.")
    clean: dict[str, int] = {}
    for name, qty in pool.items():
        n = str(name).strip().lower()
        if not NAME_RE.match(n):
            raise SandboxError(f"Resource name “{name}”: use 1–20 lowercase letters or hyphens.")
        if n in clean:
            raise SandboxError(f"The resource “{n}” is listed twice.")
        if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= MAX_QTY:
            raise SandboxError(f"“{n}” needs a whole-number quantity from 1 to {MAX_QTY}.")
        clean[n] = qty

    points = {}
    prefs = req.get("preferences") or {}
    for role in "AB":
        p = prefs.get(role)
        if not isinstance(p, dict):
            raise SandboxError(f"Set Agent {role}'s private points.")
        p = {str(k).strip().lower(): v for k, v in p.items()}
        if set(p) != set(clean):
            raise SandboxError(f"Give Agent {role} points for every resource, and only those.")
        if any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= POINTS for v in p.values()):
            raise SandboxError(f"Agent {role}'s points must be whole numbers from 0 to {POINTS}.")
        if sum(p.values()) != POINTS:
            raise SandboxError(f"Agent {role}'s points add up to {sum(p.values())}; they must add up to exactly {POINTS}.")
        points[role] = {c: p[c] for c in clean}

    strategy = req.get("strategy")
    if strategy not in INSTRUCTION_VARIANTS:
        raise SandboxError("Choose the baseline or the structured instructions.")
    first = req.get("first_mover", "A")
    if first not in ("A", "B"):
        raise SandboxError("The opening seat must be A or B.")
    return {"agents": {"A": a, "B": b}, "pool": clean, "points": points, "strategy": strategy, "first_mover": first}


def missing_keys(cfg: dict) -> list[str]:
    return sorted({MODELS[k]["key"] for k in cfg["agents"].values()
                   if k in MODELS and not os.environ.get(MODELS[k]["key"], "").strip()})


def _agent(kind: str, role: str, strategy: str) -> Agent:
    if kind == SCRIPTED:
        return MockAgent(name=f"scripted-{role}")
    cls = MODELS[kind]["cls"]
    return cls(GenerationConfig(**APPROVED_SCIENTIFIC[cls.__name__], **APPROVED_OPERATIONAL),
               instructions_variant=strategy)


class _Observed(Agent):
    """Reports each turn to the live view as the engine asks for it and
    delegates everything else, including last_call (the engine reads and
    clears it). Sees only the AgentView the engine hands to the agent."""

    def __init__(self, inner: Agent, role: str, run: "Run", pace: float):
        self._inner, self._role, self._run, self._pace = inner, role, run, pace
        self.name, self.config = inner.name, inner.config

    @property
    def last_call(self):
        return self._inner.last_call

    @last_call.setter
    def last_call(self, value):
        self._inner.last_call = value

    async def generate_response(self, view):
        self._run.set(thinking={"actor": self._role, "round": view.round_number, "since": time.time()})
        if self._pace:
            await asyncio.sleep(self._pace)
        action = await self._inner.generate_response(view)
        self._run.add_turn({"turn": view.round_number, "actor": self._role, "action": action.action_type,
                            "allocation": action.allocation, "message": action.message})
        return action


class Run:
    """One sandbox negotiation; polled by the live view."""

    def __init__(self, cfg: dict):
        self.id = uuid.uuid4().hex[:12]
        self.cfg, self.created = cfg, time.time()
        self.status, self.turns, self.thinking, self.result, self.error = "running", [], None, None, None
        self._lock = threading.Lock()

    def set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def add_turn(self, turn: dict):
        with self._lock:
            self.turns.append(turn)
            self.thinking = None

    def view(self) -> dict:
        with self._lock:
            return {"kind": "sandbox", "id": self.id, "created": self.created, "status": self.status,
                    "config": self.cfg, "labels": labels(self.cfg), "max_rounds": APPROVED_MAX_ROUNDS,
                    "turns": list(self.turns), "thinking": self.thinking,
                    "result": self.result, "error": self.error}


def labels(cfg: dict) -> dict:
    return {r: MODELS[k]["label"] if k in MODELS else f"Scripted test agent {r}" for r, k in cfg["agents"].items()}


def valuations(cfg: dict) -> dict:
    """Private points -> per-unit value (spec §2: 100 points over the whole pool)."""
    return {r: {c: cfg["points"][r][c] / q for c, q in cfg["pool"].items()} for r in "AB"}


def explain_abort(reason: str, detail: str, actor: str, cfg: dict) -> dict:
    who = f"Agent {actor} ({labels(cfg)[actor]})"
    d = detail.lower()
    if reason == "budget_exhausted":
        title, text = "Spending cap reached", f"The next turn by {who} could exceed the ${MAX_COST_USD:.2f} cap for one sandbox run."
    elif "authentication" in d or "401" in d or "permission" in d or "403" in d:
        title, text = "API key rejected", f"The provider refused the API key for {who}. Check the key in .env and restart the server."
    elif "ratelimit" in d or "429" in d:
        title, text = "Rate limited", f"The provider is rate-limiting requests for {who}. Wait a minute and try again."
    elif "timeout" in d:
        title, text = "Request timed out", f"{who} could not respond before the request timed out."
    elif "notfound" in d or "404" in d:
        title, text = "Model unavailable", f"The provider did not recognise the model for {who}."
    elif "connection" in d:
        title, text = "Provider unreachable", f"The server could not reach the provider for {who}. Check the network connection."
    else:
        title, text = "Provider error", f"The provider returned an error for {who}."
    return {"title": title, "message": f"{text} The negotiation was stopped safely. No research data was modified.",
            "detail": detail}


def summarize_result(record, run: Run) -> dict:
    row = D.summarize(record, run.id, "sandbox")
    return {**row, "invalid_detail": record.invalid_detail, "resource_pool": record.resource_pool,
            "final_allocation": record.final_allocation, "valuation_A": record.valuation_A,
            "valuation_B": record.valuation_B, "points": run.cfg["points"],
            "events": D.events_for([_turn_to_dict(t) for t in record.transcript], record.calls, record),
            "usage": {k: getattr(record, k) for k in ("api_calls", "api_attempts", "input_tokens",
                                                      "output_tokens", "latency_s", "cost_usd")}}


def execute(run: Run) -> None:
    """Run one negotiation to its end (blocking). Never raises."""
    cfg = run.cfg
    try:
        scripted = cfg["agents"]["A"] == SCRIPTED
        agents = {r: _Observed(_agent(cfg["agents"][r], r, cfg["strategy"]), r, run, SCRIPTED_PACE_S if scripted else 0)
                  for r in "AB"}
        val = valuations(cfg)
        opt_alloc, opt = compute_optimal_welfare(cfg["pool"], val["A"], val["B"])
        state = EvaluatorState(
            instance_seed=0,  # sandbox scenarios are chosen by the visitor, not drawn from a seed
            resource_pool=dict(cfg["pool"]), valuation_A=val["A"], valuation_B=val["B"],
            optimal_allocation=opt_alloc, optimal_welfare=opt, max_rounds=APPROVED_MAX_ROUNDS,
            first_mover=cfg["first_mover"], first_mover_policy="sandbox", method=cfg["strategy"],
            model_A_name=agents["A"].name, model_B_name=agents["B"].name, run_id=run.id, negotiation_index=0)
        budget = None if scripted else Budget(max_cost_usd=MAX_COST_USD, prices=APPROVED_PRICES)
        record = asyncio.run(NegotiationSession(agents["A"], agents["B"], state, budget=budget,
                                                prices=APPROVED_PRICES).run())
        run.set(status="finished", thinking=None, result=summarize_result(record, run))
    except NegotiationAborted as exc:
        run.set(status="stopped", thinking=None, error=explain_abort(exc.reason, exc.detail, exc.actor, cfg))
    except Exception as exc:  # noqa: BLE001 — shown as a safe message; the traceback goes to the server log
        traceback.print_exc()
        run.set(status="stopped", thinking=None, error={
            "title": "Unexpected error", "detail": f"{type(exc).__name__}: {exc}",
            "message": "The sandbox stopped this negotiation because of an unexpected server error. "
                       "Details are in the server log. No research data was modified."})
    finally:
        save(run)


# ---------------------------------------------------------------- store

_active: Optional[Run] = None  # ponytail: one sandbox run at a time bounds cost; a queue if several visitors share a server
_runs: dict[str, Run] = {}
_guard = threading.Lock()


def start(req, background: bool = True) -> Run:
    global _active
    cfg = validate(req)
    if missing := missing_keys(cfg):
        raise SandboxError(f"Missing API key: add {', '.join(missing)} to .env and restart the server. "
                           "No negotiation was started.")
    with _guard:
        if _active is not None:
            raise SandboxError("Another sandbox negotiation is still running. Wait for it to finish.")
        run = _active = Run(cfg)
        _runs[run.id] = run

    def go():
        global _active
        try:
            execute(run)
        finally:
            with _guard:
                _active = None

    threading.Thread(target=go, daemon=True).start() if background else go()
    return run


def save(run: Run) -> None:
    SANDBOX_DIR.mkdir(exist_ok=True)
    (SANDBOX_DIR / f"{run.id}.json").write_text(json.dumps(run.view(), default=str), encoding="utf-8")


def get(rid: str) -> Optional[dict]:
    if rid in _runs:
        return _runs[rid].view()
    path = SANDBOX_DIR / f"{rid}.json"
    if re.fullmatch(r"[0-9a-f]{12}", rid) and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def listing() -> list[dict]:
    """Finished sandbox runs, newest first, without transcripts."""
    seen = {r.id: r.view() for r in _runs.values()}
    if SANDBOX_DIR.exists():
        for p in SANDBOX_DIR.glob("*.json"):
            if p.stem not in seen:
                try:
                    seen[p.stem] = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
    out = []
    for v in seen.values():
        res = v.get("result") or {}
        out.append({k: v[k] for k in ("id", "created", "status", "config", "labels", "error")} |
                   {"result": {k: res.get(k) for k in ("outcome", "rounds", "max_rounds", "utility_A", "utility_B",
                                                       "optimal_welfare", "sw", "rwe", "ew", "equitability")} if res else None})
    return sorted(out, key=lambda v: v["created"], reverse=True)
