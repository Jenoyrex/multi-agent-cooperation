"""API keys: entered in the local app (memory only) or read from the environment or a local .env.

This module never writes a key anywhere and `.env` is read without touching os.environ, so a child process
cannot inherit one from it. `masked()` is the only form that leaves. `redact()` is applied where text leaves
the process: the store, HTTP responses and printed tracebacks.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from dotenv import dotenv_values

ENV_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
MAX_KEY_CHARS = 512
# Key-like text a provider might echo in an error, including its own partly starred form ("sk-abc***xyz").
_KEY_LIKE = re.compile(r"\b(?:sk|pk|gsk|xai|rk)-[A-Za-z0-9_*.\-]{6,}|\bgsk_[A-Za-z0-9_*]{6,}|\bAIza[A-Za-z0-9_\-*]{10,}")

_lock = threading.Lock()
_entered: dict[str, str] = {}
_removed: set[str] = set()  # a key from the environment or .env, hidden for this session
env_file_dirs: list[Path] = []  # set by the CLI to the data folder; the working directory is always read


def _dotenv() -> dict:
    values: dict = {}
    for folder in (Path.cwd(), *env_file_dirs):
        path = folder / ".env"
        if path.is_file():
            values.update({k: v for k, v in dotenv_values(path).items() if v})
    return values


def _external(connection: str) -> str:
    name = ENV_VARS.get(connection)
    return "" if not name else (os.environ.get(name, "").strip() or str(_dotenv().get(name) or "").strip())


def set_key(connection: str, key: str) -> None:
    with _lock:
        _entered[connection] = key
        _removed.discard(connection)


def remove(connection: str) -> None:
    with _lock:
        _entered.pop(connection, None)
        _removed.add(connection)


def forget_all() -> None:
    with _lock:
        _entered.clear()
        _removed.clear()


def get(connection: str) -> str:
    with _lock:
        if connection in _entered:
            return _entered[connection]
        if connection in _removed:
            return ""
    return _external(connection)


def source(connection: str) -> str | None:
    with _lock:
        if connection in _entered:
            return "entered"
        if connection in _removed:
            return None
    return "environment" if _external(connection) else None


def mask(key: str) -> str:
    """'sk-…abcd'. Short keys show nothing of themselves."""
    if len(key) < 12:
        return "…"
    return f"{key[:3]}…{key[-4:]}"


def masked(connection: str) -> str | None:
    key = get(connection)
    return mask(key) if key else None


def _known() -> list[str]:
    with _lock:
        found = list(_entered.values())
    found += [v for v in (_external(c) for c in ENV_VARS) if v]
    return sorted({k for k in found if len(k) >= 4}, key=len, reverse=True)


def redact(text: str) -> str:
    """The text without any known key, or anything shaped like one."""
    if not text:
        return text
    for key in _known():
        text = text.replace(key, "[redacted key]")
    return _KEY_LIKE.sub("[redacted key]", text)
