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
# Official pages, checked 2026-11-11 (docs/references.md). Prices and free-tier limits change: we link, never quote.
KEY_HELP = {
    "openai": {"console": "https://platform.openai.com/api-keys", "billing": "https://platform.openai.com/account/billing/overview",
               "pricing": "https://developers.openai.com/api/docs/pricing",
               "subscription": "https://help.openai.com/en/articles/8156019", "free_tier": False},
    "anthropic": {"console": "https://platform.claude.com/settings/keys", "billing": "https://platform.claude.com/settings/billing",
                  "pricing": "https://platform.claude.com/docs/en/about-claude/pricing",
                  "subscription": "https://support.claude.com/en/articles/9876003", "free_tier": False},
    "gemini": {"console": "https://aistudio.google.com/apikey", "billing": "https://ai.google.dev/gemini-api/docs/billing",
               "pricing": "https://ai.google.dev/gemini-api/docs/pricing", "subscription": None, "free_tier": True},
}
SUBSCRIPTION_NOTE = ("A chat subscription (ChatGPT Plus, Claude Pro, Gemini Advanced) does not include an API key. "
                     "API access is set up and billed separately, in the provider's developer console.")
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
            "builtin": cid in PRESETS, "needs_key": needs_key(conn), "key": keys.masked(cid), "key_source": keys.source(cid),
            "key_saved": needs_key(conn) and keys.in_env_file(cid), "key_help": KEY_HELP.get(cid)}


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


START_LOCAL = {"ollama": "Start Ollama (open the Ollama app, or run: ollama serve), then try again.",
               "lmstudio": "Open LM Studio, load a model and start its local server (Developer tab), then try again."}
# OpenAI: 429 insufficient_quota; Anthropic: 400 "credit balance is too low"; others: 402 or a billing message.
# A bare "quota" is not here: Gemini's per-minute RESOURCE_EXHAUSTED says "quota" and is a rate limit.
_QUOTA = ("insufficient_quota", "credit balance", "billing", "payment required", "exceeded your current quota")
_BAD_KEY = ("api_key_invalid", "invalid api key", "invalid x-api-key", "incorrect api key", "api key not valid", "authentication")


def classify(exc: BaseException, conn: dict) -> dict:
    """What went wrong, in words a user can act on: {kind, title, hint}. The key never appears: hints name pages, not values."""
    code = getattr(exc, "status_code", None)
    name = type(exc).__name__
    text = f"{getattr(exc, 'body', '') or ''} {exc}".lower()
    help_ = KEY_HELP.get(conn.get("id"), {})
    where = f" ({help_['billing']})" if help_.get("billing") else ""
    if "Timeout" in name:
        return {"kind": "timeout", "title": "The request timed out", "hint": "The server took too long to answer. Try again, or choose a smaller model."}
    if code is None and ("Connection" in name or "connect" in text):
        if conn.get("tier") == "local":
            return {"kind": "local_not_running", "title": f"{conn.get('name', 'The local server')} is not running",
                    "hint": START_LOCAL.get(conn.get("id"), "Start your local model server, check its address, then try again.")}
        return {"kind": "network", "title": f"Could not reach {conn.get('name', 'the provider')}", "hint": "Check your internet connection and the base URL."}
    if code == 401 or (code == 400 and any(s in text for s in _BAD_KEY)):
        return {"kind": "invalid_key", "title": "Your API key was rejected",
                "hint": "Check that you copied the whole key. You can create a new one in your provider's console" + (f" ({help_['console']})." if help_.get("console") else ".")}
    if code == 402 or (code in (400, 403, 429) and any(s in text for s in _QUOTA)):
        return {"kind": "no_credit", "title": "No credit, or billing is not set up",
                "hint": f"API use is billed separately from any chat subscription. Add credit or a payment method in your provider's billing settings{where}."}
    if code == 403:
        return {"kind": "forbidden", "title": "This key is not allowed to do that",
                "hint": "Your key or project may not have access to this model. Check its permissions in your provider's console."}
    if code == 429:
        return {"kind": "rate_limited", "title": "Rate limited", "hint": "Too many requests for now. Wait a minute and try again, or set a lower requests-per-minute limit."}
    if code == 404 or (code == 400 and "model" in text and any(s in text for s in ("not found", "does not exist", "not_found", "unknown model"))):
        return {"kind": "model_not_found", "title": "Model not found", "hint": "Click Find models and choose one from the list, and check the base URL."}
    if code is not None and code >= 500:
        return {"kind": "provider_error", "title": f"The provider had a problem ({code})", "hint": "This is on their side. Try again later."}
    return {"kind": "other", "title": f"The server answered with an error{f' ({code})' if code else ''}", "hint": "See the details below."}


def _failure(exc: Exception, conn: dict) -> dict:
    return {"ok": False, **classify(exc, conn), "status": getattr(exc, "status_code", None),
            "detail": keys.redact(f"{type(exc).__name__}: {exc}")[:300]}


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
