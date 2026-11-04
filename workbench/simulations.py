"""Simulations: run saved agents on a saved environment with the frozen engine.

A simulation is a design: repetitions (one instance seed each) x conditions (the
instruction variant each agent receives) x, optionally, swapped seats and a swapped
opening seat, so neither agent is favoured by position. Every negotiation runs
`NegotiationSession` unchanged. Nothing here touches results/; each simulation is
stored with its full configuration, agent and environment snapshots and provenance,
and is labelled by what actually ran (simulated rule-based agents, or local HTTP agents).

Infrastructure failures (an HTTP agent that does not answer) end that negotiation as
'aborted', never as an outcome, and are not re-run. Cancelling stops before the next turn.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from typing import Optional

from dashboard import data as D
from sandbox import experiment as X, keys
from sandbox.engine import EventSession
from lab import metrics as LM
from src.agents.base import Agent
from src.agents.prompting import INSTRUCTION_VARIANTS
from src.environment.allocation import Allocation
from src.negotiation.usage import summarize_calls
from src.experiments import provenance
from src.negotiation.protocol import EvaluatorState, NegotiationAborted, NegotiationSession
from src import zero_cost
from src.storage.db import _turn_to_dict
from workbench import agents as A, environments as E, store
from workbench.agents import WorkbenchError

MAX_NEGOTIATIONS, MAX_REPETITIONS = 400, 50
MAX_FAILURES_IN_A_ROW = 3
PACE_S = {"instant": 0.0, "watch": 0.35}  # 'watch' slows turns down so they can be followed; agents answer instantly
LABELS = {
    "simulated": "SIMULATED: deterministic rule-based agents. Not evidence about Claude, GPT, Gemini or any LLM.",
    "local-agents": "LOCAL AGENTS: behaviour of the HTTP programs on this machine, as they answered. Not a controlled "
                    "experiment and not evidence about any named model.",
}


class Cancelled(Exception):
    """Raised inside a negotiation when the user cancels the simulation."""


def resolve(table: str, builtin: dict, id_) -> Optional[dict]:
    return (builtin.get(id_) or store.get(table, id_)) if isinstance(id_, str) else None  # a list or dict id is a 400, not a crash


def validate(req) -> dict:
    if not isinstance(req, dict):
        raise WorkbenchError("The simulation must be a JSON object.")
    env = resolve("environments", E.BUILTIN, req.get("environment"))
    if env is None:
        raise WorkbenchError("Choose an environment.", "environment")
    picked = req.get("agents") or {}
    if not isinstance(picked, dict):
        raise WorkbenchError("Agents must be an object: {\"X\": id, \"Y\": id}.", "agents")
    defs = {}
    for label in "XY":
        d = resolve("agents", A.BUILTIN, picked.get(label))
        if d is None:
            raise WorkbenchError(f"Choose agent {label}.", f"agents.{label}")
        kind = A.KINDS[d["kind"]]
        if not kind.executable:
            raise WorkbenchError(f"Agent {label}, {d['name']}, cannot run: {kind.reason}", f"agents.{label}")
        defs[label] = d
    conds = req.get("conditions") or []
    if not isinstance(conds, list) or not conds or any(not isinstance(c, str) or c not in INSTRUCTION_VARIANTS for c in conds) or len(set(conds)) != len(conds):
        raise WorkbenchError(f"Choose one or both conditions: {', '.join(INSTRUCTION_VARIANTS)}.", "conditions")
    reps, seed = req.get("repetitions", 1), req.get("base_seed", 0)
    for v, lo, hi, what, where in ((reps, 1, MAX_REPETITIONS, "Repetitions", "repetitions"), (seed, 0, 2**31 - 1, "The base seed", "base_seed")):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise WorkbenchError(f"{what} must be a whole number from {lo} to {hi}.", where)
    swap_seats, swap_first = req.get("swap_seats", True), req.get("swap_first_mover", True)
    if not isinstance(swap_seats, bool) or not isinstance(swap_first, bool):
        raise WorkbenchError("The swap options must be true or false.", "swap_seats")
    pace = req.get("pace", "instant")
    if not isinstance(pace, str) or pace not in PACE_S:
        raise WorkbenchError("Pace must be 'instant' or 'watch'.", "pace")
    total = reps * len(conds) * (2 if swap_seats else 1) * (2 if swap_first else 1)
    if total > MAX_NEGOTIATIONS:
        raise WorkbenchError(f"This design has {total} negotiations; the limit is {MAX_NEGOTIATIONS}.", "repetitions")
    name = str(req.get("name") or "").strip() or f"{defs['X']['name']} vs {defs['Y']['name']} · {env['name']}"
    if len(name) > 80:
        raise WorkbenchError("Keep the name under 80 characters.", "name")
    cats = {A.KINDS[d["kind"]].category for d in defs.values()}
    data_kind = "simulated" if cats <= {"mock", "python"} else "local-agents"
    order = [c for c in INSTRUCTION_VARIANTS if c in conds]
    cfg = {"name": name, "environment": env, "agents": defs, "conditions": order, "repetitions": reps,
           "base_seed": seed, "swap_seats": swap_seats, "swap_first_mover": swap_first, "pace": pace,
           "data_kind": data_kind, "label": LABELS[data_kind], "total": total}
    cfg.update(X.extend(req, cfg, defs, lambda i: resolve("environments", E.BUILTIN, i)))
    if cfg["total"] > MAX_NEGOTIATIONS:
        raise WorkbenchError(f"This design has {cfg['total']} negotiations; the limit is {MAX_NEGOTIATIONS}.", "repetitions")
    return cfg


def _environments(cfg: dict) -> list[dict]:
    return cfg.get("environments") or [cfg["environment"]]  # experiments saved before sweeps have one


def plan(cfg: dict) -> list[dict]:
    """Every negotiation of the design, in run order."""
    seatings = [{"A": "X", "B": "Y"}] + ([{"A": "Y", "B": "X"}] if cfg["swap_seats"] else [])
    envs, personas = _environments(cfg), cfg.get("personas") or [None]
    out = []
    # The same seed across resource sets and personas, so a comparison between them is paired by design.
    for r in range(cfg["repetitions"]):
        for e, env in enumerate(envs):
            firsts = [env["first_mover"]] + ([("B" if env["first_mover"] == "A" else "A")] if cfg["swap_first_mover"] else [])
            for persona in personas:
                for cond in cfg["conditions"]:
                    for seats in seatings:
                        for first in firsts:
                            spec = {"index": len(out), "repetition": r, "seed": cfg["base_seed"] + r,
                                    "condition": cond, "seats": seats, "first_mover": first}
                            if len(envs) > 1:
                                spec.update(env_index=e, env_name=env["name"])
                            if persona:
                                spec["persona"] = persona["name"]
                            out.append(spec)
    return out


class _Observed(Agent):
    """Reports each turn the engine asks for, honours cancellation and pacing, and
    delegates everything else (including last_call, which the engine reads and clears)."""

    def __init__(self, inner: Agent, seat: str, sim: "Simulation"):
        self._inner, self._seat, self._sim = inner, seat, sim
        self.name, self.config = inner.name, inner.config

    @property
    def last_call(self):
        return getattr(self._inner, "last_call", None)

    @last_call.setter
    def last_call(self, value):
        self._inner.last_call = value

    async def close(self):
        closer = getattr(self._inner, "close", None)
        if closer:
            await closer()

    async def generate_response(self, view):
        if self._sim.cancel_requested:
            raise Cancelled()
        self._sim.update_live(thinking={"seat": self._seat, "round": view.round_number})
        if self._sim.pace:
            await asyncio.sleep(self._sim.pace)
        action = await self._inner.generate_response(view)
        self._sim.update_live(turn={"turn": view.round_number, "actor": self._seat, "action": action.action_type,
                                    "allocation": action.allocation, "message": action.message})
        return action


class Simulation:
    def __init__(self, cfg: dict):
        self.id, self.created = store.new_id(), time.time()
        self.cfg, self.plan = cfg, plan(cfg)
        self.pace = PACE_S[cfg["pace"]]
        self.status, self.trials, self.log, self.error, self.live = "running", [], [], None, None
        self.cancel_requested, self.finished, self.stop_reason = False, None, None
        self.provenance = {"protocol_id": provenance.PROTOCOL_ID, "spec_version": provenance.spec_version(),
                           "prompt_hash": provenance.prompt_hash(), "code_version": provenance.code_version(),
                           "engine": ("sandbox.engine.EventSession (sandbox_v1)" if cfg.get("events") else "src.negotiation.protocol.NegotiationSession (unchanged)"),
                           "sandbox_protocol": cfg.get("sandbox_protocol"), "zero_cost_mode": not zero_cost.paid_calls_allowed()}
        self._lock = threading.Lock()

    def note(self, text: str, level: str = "info"):
        with self._lock:
            self.log.append({"t": round(time.time() - self.created, 3), "level": level, "text": text})

    def update_live(self, thinking=None, turn=None):
        with self._lock:
            if turn:
                self.live["turns"].append(turn)
            self.live["thinking"] = thinking

    def doc(self) -> dict:
        with self._lock:
            return {"id": self.id, "created": self.created, "finished": self.finished, "status": self.status,
                    "config": self.cfg, "plan_size": len(self.plan), "trials": list(self.trials), "log": list(self.log),
                    "error": self.error, "provenance": self.provenance, "stop_reason": self.stop_reason,
                    "live": None if self.live is None else {**self.live, "turns": list(self.live["turns"])}}


def _claims(alloc: Optional[dict], seat: str, pool: dict) -> Optional[dict]:
    return None if not alloc else {c: alloc[seat][c] / q for c, q in pool.items()}


def _pool_at(turn: int, pool: dict, fired: list[dict]) -> dict:
    """The starting resources, with the units they had on this turn (an event can add units to one)."""
    for ev in fired:
        if ev["turn"] <= turn:
            pool = {c: ev["pool_after"][c] for c in pool}
    return pool


def _usage(calls: list[dict], cfg: dict) -> dict:
    return summarize_calls(calls, X.price_map(cfg))


async def _run_session(session, agents: list) -> object:
    try:
        return await session.run()
    finally:
        for agent in agents:  # model clients and Python processes belong to this negotiation's event loop
            await agent.close()


def _trial(spec: dict, record, inst: dict, label_of: dict, fired: Optional[list] = None, cfg: Optional[dict] = None) -> dict:
    fired = fired or []
    seat_of = {label: seat for seat, label in spec["seats"].items()}
    transcript = [_turn_to_dict(t) for t in record.transcript]
    row = D.summarize(record, f"{spec['index']}", "simulation")
    opening = {}
    for label, seat in seat_of.items():
        first = next((t for t in transcript if t["actor"] == seat and t["action"]["action_type"] == "OFFER"), None)
        opening[label] = None if first is None else {"turn": first["turn_number"],
                                                      "shares": _claims(first["action"]["allocation"], seat, _pool_at(first["turn_number"], inst["pool"], fired))}
    return {**spec, "status": "completed", **{k: row[k] for k in (
                "outcome", "invalid_reason", "rounds", "utility_A", "utility_B", "optimal_welfare", "rwe", "ew",
                "sw", "swe", "equitability", "envy_free", "first_mover_gap")},
            "invalid_detail": record.invalid_detail, "pool": inst["pool"], "points": inst["points"],
            "optimal_allocation": inst["optimal_allocation"], "final_allocation": record.final_allocation,
            "events": D.events_for(transcript, record.calls, record),
            "usage": {k: getattr(record, k) for k in ("api_calls", "api_attempts", "input_tokens", "output_tokens", "latency_s", "cost_usd")},
            "final_pool": record.resource_pool, "final_valuation": {"A": record.valuation_A, "B": record.valuation_B},
            "valuation": inst["valuation"], "events_fired": fired, "pre_event_optimal_welfare": inst["optimal_welfare"],
            "agents": {label: {"seat": seat, "name": label_of[label],
                               "utility": (record.utility_A if seat == "A" else record.utility_B) / 100,
                               "behavior": LM.behavior(record, seat), "opening": opening[label],
                               "final_shares": _claims(record.final_allocation, seat, _pool_at(record.num_rounds, inst["pool"], fired)),
                               "ended": LM.ended_by(record) == seat} for label, seat in seat_of.items()}}


def execute(sim: Simulation) -> None:
    """Run every planned negotiation (blocking). Never raises."""
    cfg, envs = sim.cfg, _environments(sim.cfg)
    label_of = {k: d["name"] for k, d in cfg["agents"].items()}
    events, prices = cfg.get("events") or [], X.price_map(cfg)
    budget = X.budget_for(cfg)
    persona_text = {p["name"]: p["text"] for p in cfg.get("personas") or []}
    models = any(X.is_model(d) for d in cfg["agents"].values())
    failures = 0  # in a row; a wrong key or a stopped server would fail every remaining negotiation the same way
    sim.note(f"Started: {len(sim.plan)} negotiations. {cfg['label']}")
    try:
        for spec in sim.plan:
            if sim.cancel_requested:
                break
            env = envs[spec.get("env_index", 0)]
            inst = E.instance(env, spec["seed"])
            names = {seat: f"{label}:{label_of[label]}" for seat, label in spec["seats"].items()}

            def definition(label: str) -> dict:
                d = cfg["agents"][label]
                return {**d, "params": {**d["params"], "persona": persona_text[spec["persona"]]}} if X.is_model(d) and spec.get("persona") else d
            built = {seat: _Observed(A.build(definition(label), names[seat], spec["condition"]), seat, sim)
                     for seat, label in spec["seats"].items()}
            state = EvaluatorState(
                instance_seed=spec["seed"], resource_pool=inst["pool"], valuation_A=inst["valuation"]["A"],
                valuation_B=inst["valuation"]["B"], optimal_allocation=Allocation(a_A=inst["optimal_allocation"]["A"], a_B=inst["optimal_allocation"]["B"]),
                optimal_welfare=inst["optimal_welfare"], max_rounds=env["max_rounds"], first_mover=spec["first_mover"],
                first_mover_policy="explicit", method=spec["condition"], model_A_name=names["A"], model_B_name=names["B"],
                run_id=sim.id, negotiation_index=spec["index"])
            with sim._lock:
                sim.live = {"index": spec["index"], "spec": spec, "pool": inst["pool"], "turns": [], "thinking": None}
            where = (f"#{spec['index'] + 1} (seed {spec['seed']}, {spec['condition']}, {spec['seats']['A']} in seat A, "
                     f"{spec['first_mover']} opens)")
            try:
                if events:
                    session = EventSession(built["A"], built["B"], seed=spec["seed"], pool=inst["pool"], valuation=inst["valuation"],
                                           optimal_welfare=inst["optimal_welfare"], max_rounds=env["max_rounds"], first_mover=spec["first_mover"],
                                           method=spec["condition"], model_A=names["A"], model_B=names["B"], events=events, run_id=sim.id,
                                           index=spec["index"], budget=budget, prices=prices)
                else:
                    session = NegotiationSession(built["A"], built["B"], state, budget=budget, prices=prices)
                record = asyncio.run(_run_session(session, list(built.values())))
                fired = session.fired if events else []
                trial = _trial(spec, record, inst, label_of, fired, cfg)
                failures = 0
                sim.note(f"Negotiation {where}: {record.outcome.replace('_', ' ')} after {record.num_rounds} turn(s).")
            except NegotiationAborted as exc:
                trial = {**spec, "status": "aborted", "reason": exc.reason, "detail": keys.redact(exc.detail), "round": exc.round_number,
                         "actor": exc.actor, "pool": inst["pool"], "usage": _usage(exc.calls, cfg)}
                sim.note(f"Negotiation {where}: aborted ({exc.reason}) in turn {exc.round_number}; not an outcome.", "warn")
                failures += 1
                if exc.reason == "budget_exhausted":
                    sim.stop_reason = f"The cap was reached: {exc.detail}"
                elif models and failures >= MAX_FAILURES_IN_A_ROW:
                    sim.stop_reason = f"Stopped after {failures} connection failures in a row. Last: {keys.redact(exc.detail)}"
            except Cancelled:
                sim.note(f"Negotiation {where}: cancelled before its next turn; not an outcome.", "warn")
                with sim._lock:
                    sim.trials.append({**spec, "status": "cancelled", "pool": inst["pool"]})
                break
            with sim._lock:
                sim.trials.append(trial)
            store.put("simulations", sim.doc())
            if sim.stop_reason:
                sim.note(sim.stop_reason, "warn")
                break
        with sim._lock:
            # A negotiation cut short by cancelling is not a completed plan, even when it was the last one.
            stopped = len(sim.trials) < len(sim.plan) or any(t["status"] == "cancelled" for t in sim.trials)
            sim.status = "cancelled" if (sim.cancel_requested or sim.stop_reason) and stopped else "completed"
    except Exception as exc:  # noqa: BLE001 — recorded on the simulation; traceback to the server log
        print(keys.redact(traceback.format_exc()), file=sys.stderr)
        with sim._lock:
            sim.status, sim.error = "failed", keys.redact(f"{type(exc).__name__}: {exc}")
        sim.note(f"Stopped by an unexpected error: {type(exc).__name__}. Details are in the server log.", "error")
    finally:
        with sim._lock:
            sim.finished, sim.live = time.time(), None
        sim.note({"completed": "Completed.", "cancelled": "Cancelled.", "failed": "Failed."}.get(sim.status, sim.status))
        store.put("simulations", sim.doc())
        _finish(sim)


_guard = threading.Lock()
_active: Optional[Simulation] = None


def _finish(sim: Simulation) -> None:
    global _active
    with _guard:
        if _active is sim:
            _active = None


def start(req, background: bool = True) -> Simulation:
    global _active
    cfg = validate(req)
    X.check_consent(req, cfg)
    with _guard:
        if _active is not None:
            raise WorkbenchError("Another simulation is still running. Wait for it to finish or cancel it.", status=409)
        sim = _active = Simulation(cfg)
    store.put("simulations", sim.doc())
    if background:
        threading.Thread(target=execute, args=(sim,), daemon=True).start()
    else:
        execute(sim)
    return sim


def get(id_: str) -> Optional[dict]:
    with _guard:
        active = _active if _active is not None and _active.id == id_ else None
    return active.doc() if active else store.get("simulations", id_)


def cancel(id_: str) -> dict:
    with _guard:
        sim = _active if _active is not None and _active.id == id_ else None
    if sim is None:
        raise WorkbenchError("Only a running simulation can be cancelled.", status=409)
    sim.cancel_requested = True
    sim.note("Cancellation requested; stopping before the next turn.", "warn")
    return {"id": id_, "cancel_requested": True}
