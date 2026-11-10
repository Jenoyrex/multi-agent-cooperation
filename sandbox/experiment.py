"""What the sandbox adds to an experiment's design: sweeps, personas, resource events, prices, caps, labels, consent.

Old saved experiments have none of these fields; every reader falls back to the old single-environment shape.
Prices only ever come from the user. A hard token cap is mandatory whenever an API key will be used.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Optional

from dashboard import demo
from sandbox import keys, policy, providers
from sandbox.engine import PROTOCOL
from src.agents.base import AgentView, TranscriptTurn
from src.agents.schema import NegotiationAction
from src.negotiation.usage import Budget
from workbench.agents import WorkbenchError

MAX_ENVS, MAX_PERSONAS, MAX_EVENTS, PERSONA_CHARS = 8, 6, 3, 4000
MAX_EXTEND = 10
CHARS_TYPICAL, CHARS_WORST = 4, 2  # rough characters per token, for the estimate and for the per-call ceiling
TYPICAL_MESSAGE, WORST_MESSAGE = 100, 1000
DEMO_FIRST = "http_local"  # keeps its own demo message (workbench/api.py)
CONSENT_TEXT = {
    "api_cost": "Run (uses your API key, you pay)",
    "remote_http": "Send this experiment's turns to the remote HTTP agent's server",
    "python_file": "Run this file on my computer",
}
SIMULATED = "Rule-based (simulated)"
HEADS = {"api-model": "API MODEL", "local-model": "LOCAL MODEL", "local-agents": "YOUR AGENTS"}


def is_model(defn: dict) -> bool:
    return defn["kind"] == "prompt"


def connection_of(defn: dict) -> dict:
    return providers.require(defn["params"]["connection"], "agents")


def source_label(defn: dict) -> str:
    kind = defn["kind"]
    if kind == "prompt":
        conn = connection_of(defn)
        return f"Local model: {defn['params']['model']}" if conn["tier"] == "local" else f"API model: {conn['name']}/{defn['params']['model']}"
    if kind == "http_local":
        return "HTTP agent on this computer"
    if kind == "http_remote":
        return f"Remote HTTP agent: {defn['params']['url']}"
    if kind == "python_file":
        return f"Python file: {defn['upload']['filename']}"
    return SIMULATED


def check_agents(defs: dict) -> None:
    """Refuses before anything is built: local-only kinds, and a model whose key is missing."""
    from workbench.agents import LOCAL_ONLY
    for label, d in defs.items():
        if d["kind"] in LOCAL_ONLY and not (d["kind"] == DEMO_FIRST and demo.enabled()):
            policy.require_local(d["name"])
        if is_model(d):
            conn = connection_of(d)
            if providers.needs_key(conn) and not keys.get(conn["id"]):
                raise WorkbenchError(f"Agent {label}, {d['name']}: add your API key for {conn['name']} first.", f"agents.{label}")


def _int(v, lo, hi, what, where) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise WorkbenchError(f"{what} must be a whole number from {lo} to {hi}.", where)
    return v


def _events(raw, envs: list[dict]) -> list[dict]:
    from workbench.environments import MAX_QTY, NAME_RE
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_EVENTS:
        raise WorkbenchError(f"Add at most {MAX_EVENTS} resource events.", "events")
    # A later turn must still allow an answer, in the shortest negotiation of the sweep.
    last = min(e["max_rounds"] for e in envs) - 1
    out, turns = [], set()
    for i, ev in enumerate(raw):
        where = f"events.{i}"
        if not isinstance(ev, dict):
            raise WorkbenchError("Each resource event must be an object.", where)
        if last < 2:
            raise WorkbenchError("These resource sets are too short for an event: an event needs a round limit of at least 3.", where)
        turn = _int(ev.get("turn"), 2, last, "The event's turn", f"{where}.turn")
        if turn in turns:
            raise WorkbenchError("Two events cannot start the same turn.", f"{where}.turn")
        turns.add(turn)
        cat = str(ev.get("category") or "").strip().lower()
        if not NAME_RE.match(cat):
            raise WorkbenchError("Name the new resource with 1 to 20 lowercase letters or hyphens.", f"{where}.category")
        out.append({"turn": turn, "category": cat, "units": _int(ev.get("units"), 1, MAX_QTY, "The event's units", f"{where}.units"),
                    "max_points": _int(ev.get("max_points", 40), 0, 100, "The largest private value", f"{where}.max_points"),
                    "extend_rounds": _int(ev.get("extend_rounds", 0), 0, MAX_EXTEND, "The deadline extension", f"{where}.extend_rounds")})
    return sorted(out, key=lambda e: e["turn"])


def _personas(raw, defs: dict) -> list[dict]:
    if raw in (None, []):
        return []
    if not any(is_model(d) for d in defs.values()):
        raise WorkbenchError("Personas change prompt agents only, and neither agent is one.", "personas")
    if not isinstance(raw, list) or len(raw) > MAX_PERSONAS:
        raise WorkbenchError(f"Add at most {MAX_PERSONAS} personas.", "personas")
    out, names = [], set()
    for i, p in enumerate(raw):
        name, text = (str(p.get(k) or "").strip() for k in ("name", "text")) if isinstance(p, dict) else ("", "")
        if not 1 <= len(name) <= 40 or len(text) > PERSONA_CHARS or name in names:
            raise WorkbenchError(f"Each persona needs its own name of 1 to 40 characters, and at most {PERSONA_CHARS} characters of text.", f"personas.{i}")
        names.add(name)
        out.append({"name": name, "text": text})
    return out


def _prices(raw, defs: dict) -> dict:
    """{model: [per 1M input, per 1M output]}, in the currency the user thinks in. Only what the user typed."""
    if raw in (None, {}):
        return {}
    wanted = {d["params"]["model"] for d in defs.values() if is_model(d)}
    if not isinstance(raw, dict) or set(raw) - wanted:
        raise WorkbenchError("Prices can only be given for this experiment's models.", "prices")
    out = {}
    for model, pair in raw.items():
        if (not isinstance(pair, list) or len(pair) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 100_000 for v in pair)):
            raise WorkbenchError(f"Give {model}'s price as two numbers: per million input tokens and per million output tokens.", f"prices.{model}")
        out[model] = [float(pair[0]), float(pair[1])]
    return out


def _caps(raw) -> dict:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise WorkbenchError("Caps must be an object.", "caps")
    out = {}
    if raw.get("max_tokens") is not None:
        out["max_tokens"] = _int(raw["max_tokens"], 1, 10**10, "The token cap", "caps.max_tokens")
    if raw.get("max_cost") is not None:
        v = raw["max_cost"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise WorkbenchError("The spending cap must be a number above 0.", "caps.max_cost")
        out["max_cost"] = float(v)
    return out


def extend(req: dict, cfg: dict, defs: dict, resolve_env) -> dict:
    """Adds the sandbox design to a validated config. `resolve_env` looks an id up."""
    raw = req.get("environments")
    envs = [cfg["environment"]]
    if raw not in (None, []):
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ENVS or len(set(map(str, raw))) != len(raw):
            raise WorkbenchError(f"Choose 1 to {MAX_ENVS} different resource sets.", "environments")
        envs = [resolve_env(i) for i in raw]
        if any(e is None for e in envs):
            raise WorkbenchError("One of those resource sets no longer exists.", "environments")
    check_agents(defs)
    events = _events(req.get("events"), envs)
    personas, prices, caps = _personas(req.get("personas"), defs), _prices(req.get("prices"), defs), _caps(req.get("caps"))
    labels = {k: source_label(d) for k, d in defs.items()}
    tiers = {connection_of(d)["tier"] for d in defs.values() if is_model(d)}
    new_kinds = any(d["kind"] in ("prompt", "http_remote", "python_file") for d in defs.values())
    extra = {"environment": envs[0], "environments": envs, "events": events, "personas": personas, "prices": prices, "caps": caps,
             "source_labels": labels, "sandbox_protocol": PROTOCOL if events else None,
             "total": cfg["total"] * len(envs) * max(1, len(personas))}
    if len(envs) > 1 and not str(req.get("name") or "").strip():
        extra["name"] = f"{defs['X']['name']} vs {defs['Y']['name']} · {len(envs)} resource sets"
    if new_kinds:
        kind = "api-model" if "api" in tiers else "local-model" if tiers else "local-agents"
        names = " vs ".join(dict.fromkeys(labels.values()))
        extra.update(data_kind=kind, label=f"{HEADS[kind]}: {names}. Exploratory sandbox data about these agents on these resources. "
                                           "Not the research study's results.")
    return extra


# ------------------------------------------------------------------ estimate
def _sample_view(env: dict, rounds: int, chars: int) -> AgentView:
    """A worst-length view for sizing the prompt: the last turn, every earlier turn an offer with a message."""
    pool = dict(env["resources"]) if env["source"] == "custom" else {f"resource-{i}": 100 for i in range(env["categories"])}
    alloc = {s: {c: 0 for c in pool} for s in "AB"}
    action = NegotiationAction(action_type="OFFER", allocation=alloc, message="x" * chars)
    return AgentView(role="A", resource_pool=pool, own_valuation={c: 1.0 for c in pool},
                     transcript_so_far=[TranscriptTurn(i + 1, "A", action) for i in range(rounds - 1)],
                     round_number=rounds, rounds_remaining=0, max_rounds=rounds)


def _prompt_chars(defn: dict, cfg: dict, env: dict, rounds: int, message_chars: int) -> int:
    from sandbox.model_agent import system_text, user_text
    view = _sample_view(env, rounds, message_chars)
    persona = max([defn["params"].get("persona") or ""] + [p["text"] for p in cfg.get("personas") or []], key=len)
    extra = sum(len(e["category"]) + 200 for e in cfg.get("events") or [])
    return max(len(system_text(view, c, persona)) for c in cfg["conditions"]) + len(user_text(view)) + extra


def estimate(cfg: dict) -> dict:
    """How much the design could use. Tokens are a rough estimate from prompt length; cost uses only the prices the user typed."""
    from sandbox.model_agent import MAX_ATTEMPTS
    envs = cfg.get("environments") or [cfg["environment"]]
    extend_by = sum(e["extend_rounds"] for e in cfg.get("events") or [])
    per_env = cfg["total"] // len(envs)
    rows = []
    for label, d in cfg["agents"].items():
        if not is_model(d):
            continue
        conn, model = connection_of(d), d["params"]["model"]
        calls, typical_in, ceiling, out = 0, 0, 0, int(d["params"].get("max_output_tokens", 400))
        for env in envs:
            rounds = env["max_rounds"] + extend_by
            n = per_env * math.ceil(rounds / 2)  # an agent moves at most every other turn
            calls += n
            typical_in = max(typical_in, _prompt_chars(d, cfg, env, rounds, TYPICAL_MESSAGE) // CHARS_TYPICAL)
            ceiling = max(ceiling, _prompt_chars(d, cfg, env, rounds, WORST_MESSAGE) // CHARS_WORST)
        price = (cfg.get("prices") or {}).get(model)
        tokens = calls * (typical_in + out)
        rows.append({"label": label, "name": d["name"], "model": model, "connection": conn["name"], "tier": conn["tier"], "source": cfg["source_labels"][label],
                     "calls_max": calls, "input_per_call": typical_in, "output_per_call_max": out, "tokens_estimate": tokens,
                     "input_ceiling": ceiling, "reserve_per_call": MAX_ATTEMPTS * (ceiling + out), "price": price,
                     "cost_estimate": None if price is None else calls * (typical_in * price[0] + out * price[1]) / 1_000_000})
    api = [r for r in rows if r["tier"] == "api"]
    capped = api or rows
    tokens = sum(r["tokens_estimate"] for r in capped)
    reserve = max((r["reserve_per_call"] for r in capped), default=0)
    return {"agents": rows, "negotiations": cfg["total"], "api_tokens_estimate": sum(r["tokens_estimate"] for r in api),
            "cost_estimate": None if not api or any(r["cost_estimate"] is None for r in api) else sum(r["cost_estimate"] for r in api),
            "missing_prices": sorted({r["model"] for r in api if r["price"] is None}), "reserve_per_call": reserve,
            "suggested_token_cap": tokens + reserve if capped else None,
            "note": "A rough estimate from prompt length, at the most turns each negotiation could take. Providers count tokens their own way, "
                    "and retries are extra. Cost uses only the prices you entered."}


def required_consents(cfg: dict) -> list[str]:
    kinds = {d["kind"] for d in cfg["agents"].values()}
    tiers = {connection_of(d)["tier"] for d in cfg["agents"].values() if is_model(d)}
    return [c for c, on in (("api_cost", "api" in tiers), ("remote_http", "http_remote" in kinds), ("python_file", "python_file" in kinds)) if on]


def consent_token(cfg: dict) -> str:
    """Names this exact design, so a consent given for one design cannot start a different one."""
    keep = {k: cfg.get(k) for k in ("environments", "environment", "events", "personas", "prices", "caps", "conditions", "repetitions",
                                    "base_seed", "swap_seats", "swap_first_mover", "total")}
    keep["agents"] = {k: {f: d.get(f) for f in ("kind", "params", "upload")} for k, d in cfg["agents"].items()}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:32]


def check_caps(cfg: dict, est: Optional[dict] = None) -> None:
    est = est or estimate(cfg)
    caps, api = cfg.get("caps") or {}, [r for r in est["agents"] if r["tier"] == "api"]
    if api and "max_tokens" not in caps:
        raise WorkbenchError(f"Set a token cap to use an API key. A suggestion for this design: {est['suggested_token_cap']}.", "caps.max_tokens")
    if est["agents"] and "max_tokens" in caps and caps["max_tokens"] < est["reserve_per_call"]:
        raise WorkbenchError(f"This cap is too small for even one request, which could use up to {est['reserve_per_call']} tokens with retries.", "caps.max_tokens")
    if "max_cost" in caps and est["missing_prices"]:
        raise WorkbenchError(f"A spending cap needs your prices for: {', '.join(est['missing_prices'])}.", "prices")


def check_consent(req: dict, cfg: dict) -> None:
    """Called when a run starts. Nothing is sent unless the user agreed to this exact design."""
    needed = required_consents(cfg)
    check_caps(cfg)
    if not needed:
        return
    given = req.get("consent") if isinstance(req.get("consent"), dict) else {}
    if given.get("token") != consent_token(cfg):
        raise WorkbenchError("Review the estimate and confirm this exact experiment first.", "consent", 409)
    accepted = given.get("accepted")
    missing = [c for c in needed if not isinstance(accepted, list) or c not in accepted]
    if missing:
        raise WorkbenchError(f"Please confirm: {'; '.join(CONSENT_TEXT[c] for c in missing)}.", "consent", 409)


# ------------------------------------------------------------------ runtime
class SandboxBudget(Budget):
    """The frozen Budget, counting only the capped models. Rule-based and HTTP agents have no token usage to count."""

    def add(self, call: dict) -> None:
        if call.get("model") in self.reserve:
            super().add(call)


def price_map(cfg: dict) -> Optional[dict]:
    return {m: tuple(p) for m, p in (cfg.get("prices") or {}).items()} or None


def budget_for(cfg: dict) -> Optional[SandboxBudget]:
    """Shared across the whole experiment. Checked before every model call, with the next call's worst case reserved."""
    caps = cfg.get("caps") or {}
    if not caps:
        return None
    from sandbox.model_agent import MAX_ATTEMPTS
    rows = estimate(cfg)["agents"]
    capped = [r for r in rows if r["tier"] == "api"] or rows
    if not capped:
        return None
    reserve = {r["model"]: (r["input_ceiling"], r["output_per_call_max"], MAX_ATTEMPTS) for r in capped}
    return SandboxBudget(max_total_tokens=caps.get("max_tokens"), max_cost_usd=caps.get("max_cost"), prices=price_map(cfg), reserve=reserve)
