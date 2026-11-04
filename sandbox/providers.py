"""Model connections: where a prompt agent's requests go.

Presets come from each provider's current official docs (docs/sandbox-v2-plan.md lists the sources). A custom
connection is saved without its key. `client()` is the one place a provider SDK client is built, and it refuses
in the public demo. Nothing here sends a request unless the user clicked Test or Refresh, or started a run.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from sandbox import keys, policy
from workbench import store
from workbench.agents import WorkbenchError

STRUCTURED = ("json_schema", "json_object", "none")
TIMEOUT_S = 60.0
PRESETS = {p["id"]: p for p in (
    {"id": "ollama", "name": "Ollama", "tier": "local", "api": "openai", "base_url": "http://localhost:11434/v1", "structured": "json_schema",
     "tokens_param": "max_tokens", "help": "Free, on this computer. Install it from https://ollama.com, then run: ollama pull llama3.2"},
    {"id": "lmstudio", "name": "LM Studio", "tier": "local", "api": "openai", "base_url": "http://localhost:1234/v1", "structured": "json_schema",
     "tokens_param": "max_tokens", "help": "Free, on this computer. Install it from https://lmstudio.ai, load a model and start its local server."},
    {"id": "openai", "name": "OpenAI", "tier": "api", "api": "openai", "base_url": "https://api.openai.com/v1", "structured": "json_schema",
     "tokens_param": "max_completion_tokens", "help": "Your own OpenAI key. You pay OpenAI directly."},
    {"id": "anthropic", "name": "Anthropic (Claude)", "tier": "api", "api": "anthropic", "base_url": "", "structured": "json_schema",
     "tokens_param": "max_tokens", "help": "Your own Anthropic key. You pay Anthropic directly."},
    {"id": "gemini", "name": "Google Gemini", "tier": "api", "api": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "structured": "json_schema", "tokens_param": "max_tokens", "help": "Your own Gemini key, through Google's OpenAI-compatible endpoint (which Google calls beta). You pay Google directly."},
)}
CUSTOM_HELP = {"local": "An OpenAI-compatible server on this computer.",
               "api": "Any OpenAI-compatible provider, such as Groq (https://api.groq.com/openai/v1) or OpenRouter (https://openrouter.ai/api/v1). You pay them directly."}


def validate(doc) -> dict:
    """A clean custom connection: never carries a key."""
    if not isinstance(doc, dict):
        raise WorkbenchError("The connection must be a JSON object.")
    name = str(doc.get("name") or "").strip()
    if not 1 <= len(name) <= 40:
        raise WorkbenchError("Give the connection a name of 1 to 40 characters.", "name")
    tier = doc.get("tier")
    if tier not in ("local", "api"):
        raise WorkbenchError("Choose local or API.", "tier")
    structured = doc.get("structured", "json_object")
    if structured not in STRUCTURED:
        raise WorkbenchError(f"Structured output must be one of: {', '.join(STRUCTURED)}.", "structured")
    rpm = doc.get("rpm", 0)
    if isinstance(rpm, bool) or not isinstance(rpm, int) or not 0 <= rpm <= 6000:
        raise WorkbenchError("Requests per minute must be a whole number from 0 (no limit) to 6000.", "rpm")
    return {"name": name, "tier": tier, "api": "openai", "structured": structured, "rpm": rpm, "tokens_param": "max_tokens",
            "base_url": policy.clean_base_url(doc.get("base_url"), local_only=tier == "local"), "help": CUSTOM_HELP[tier]}


def resolve(connection_id) -> Optional[dict]:
    if not isinstance(connection_id, str):
        return None
    if connection_id in PRESETS:
        return {"rpm": 0, **PRESETS[connection_id]}
    return store.get("connections", connection_id)


def require(connection_id, where: str = "params.connection") -> dict:
    conn = resolve(connection_id)
    if conn is None:
        raise WorkbenchError("Choose a model connection.", where)
    return conn


def needs_key(conn: dict) -> bool:
    return conn["tier"] == "api"


def public(conn: dict) -> dict:
    """What the page may see: a masked key at most."""
    cid = conn["id"]
    return {**{k: conn.get(k) for k in ("id", "name", "tier", "api", "base_url", "structured", "rpm", "help")},
            "builtin": cid in PRESETS, "needs_key": needs_key(conn), "key": keys.masked(cid), "key_source": keys.source(cid)}


def listing() -> list[dict]:
    return [public(resolve(c)) for c in PRESETS] + [public(c) for c in store.all_("connections")]


def client(conn: dict, timeout_s: float = TIMEOUT_S):
    """A fresh provider SDK client with SDK retries off (retries are counted by the caller)."""
    policy.require_local("Calling a model")
    key = keys.get(conn["id"])
    if needs_key(conn) and not key:
        raise WorkbenchError(f"Add your API key for {conn['name']} first.", "key")
    if conn["api"] == "anthropic":
        import anthropic  # local import: the public demo host does not install the SDKs
        return anthropic.AsyncAnthropic(api_key=key, timeout=timeout_s, max_retries=0, base_url=conn.get("base_url") or None)
    import openai
    # A local server ignores the key, but the SDK requires a value (Ollama's docs say the same).
    return openai.AsyncOpenAI(api_key=key or "local", base_url=conn["base_url"], timeout=timeout_s, max_retries=0)


def _failure(exc: Exception, conn: dict) -> dict:
    code = getattr(exc, "status_code", None)
    name = type(exc).__name__
    title, hint = {401: ("The key was rejected", "Check the API key."), 403: ("Not allowed", "This key may not use that model."),
                   404: ("Not found", "Check the base URL and the model name."),
                   429: ("Rate limited or out of credit", "Wait a moment, or check your provider account.")}.get(
        code, ("The request timed out", "The server took too long to answer.") if "Timeout" in name else
        ("Could not reach the server", "Is it running?" if conn["tier"] == "local" else "Check your connection and the base URL.")
        if "Connection" in name else (f"The server answered with an error{f' ({code})' if code else ''}", ""))
    return {"ok": False, "title": title, "hint": hint, "status": code, "detail": keys.redact(f"{name}: {exc}")[:300]}


async def _close(cl) -> None:
    try:
        await cl.close()
    except Exception:  # noqa: BLE001 — closing never changes the answer
        pass


def list_models(connection_id) -> dict:
    """The server's own model list. Only when the user asks."""
    policy.require_local("Listing models")
    conn = require(connection_id, "connection")

    async def go():
        cl = client(conn, timeout_s=15.0)
        try:
            page = await cl.models.list()
            return {"ok": True, "models": sorted(m.id for m in page.data)}
        except WorkbenchError:
            raise
        except Exception as exc:  # noqa: BLE001 — reported to the user, redacted
            return {**_failure(exc, conn), "models": []}
        finally:
            await _close(cl)
    return asyncio.run(go())


def test_connection(connection_id, model) -> dict:
    """One tiny request, only when clicked: a few tokens, sent to the user's own provider."""
    policy.require_local("Testing a connection")
    conn = require(connection_id, "connection")
    if not isinstance(model, str) or not 1 <= len(model.strip()) <= 200:
        raise WorkbenchError("Choose a model to test.", "model")
    model, msgs = model.strip(), [{"role": "user", "content": "Reply with the single word OK."}]

    async def go():
        cl = client(conn, timeout_s=30.0)
        start = time.monotonic()
        try:
            if conn["api"] == "anthropic":
                r = await cl.messages.create(model=model, max_tokens=16, messages=msgs)
                used = (r.usage.input_tokens, r.usage.output_tokens) if getattr(r, "usage", None) else (None, None)
            else:
                r = await cl.chat.completions.create(model=model, messages=msgs, **{conn["tokens_param"]: 16})
                if not getattr(r, "choices", None):
                    return {"ok": False, "title": "The reply was not understood", "hint": "This server may not be OpenAI-compatible.", "status": None, "detail": ""}
                used = (r.usage.prompt_tokens, r.usage.completion_tokens) if getattr(r, "usage", None) else (None, None)
            return {"ok": True, "title": "Connected", "model": model, "latency_s": round(time.monotonic() - start, 2),
                    "input_tokens": used[0], "output_tokens": used[1]}
        except WorkbenchError:
            raise
        except Exception as exc:  # noqa: BLE001 — reported to the user, redacted
            return _failure(exc, conn)
        finally:
            await _close(cl)
    return asyncio.run(go())
