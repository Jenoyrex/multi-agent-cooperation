"""API keys: entered in the local app (memory only), read from the environment or a local .env, or saved by the
user to the data folder's .env.

`.env` is read without touching os.environ, so a child process cannot inherit a key from it. Saving writes one line
to a `.env` that git ignores (refused otherwise) and nothing else. `masked()` is the only form that leaves.
`redact()` is applied where text leaves the process: the store, HTTP responses and printed tracebacks.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from dotenv import dotenv_values, set_key as _dotenv_set, unset_key as _dotenv_unset

ENV_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
MAX_KEY_CHARS = 512
# Key-like text a provider might echo in an error, including its own partly starred form ("sk-abc***xyz").
_KEY_LIKE = re.compile(r"\b(?:sk|pk|gsk|xai|rk)-[A-Za-z0-9_*.\-]{6,}|\bgsk_[A-Za-z0-9_*]{6,}|\bAIza[A-Za-z0-9_\-*]{10,}")

_lock = threading.Lock()
_entered: dict[str, str] = {}
_removed: set[str] = set()  # a key from the environment or .env, hidden for this session
_hide_external = False  # "Forget all keys": every environment/.env key hidden for this session
env_file_dirs: list[Path] = []  # set by the CLI to the data folder; the working directory is always read


class KeyFileError(ValueError):
    """Saving to .env was refused; the message says why and what to do."""


def env_name(connection: str) -> str:
    """The variable a key is read from: the provider's usual name, or one per custom connection."""
    return ENV_VARS.get(connection) or f"COOPERATION_LAB_KEY_{re.sub(r'[^A-Za-z0-9]', '_', connection).upper()}"


def env_file() -> Path:
    """Where "Save to my .env" writes: the data folder, or the working directory when run from source."""
    return (env_file_dirs[0] if env_file_dirs else Path.cwd()) / ".env"


def _dotenv() -> dict:
    values: dict = {}
    for folder in (Path.cwd(), *env_file_dirs):
        path = folder / ".env"
        if path.is_file():
            values.update({k: v for k, v in dotenv_values(path).items() if v})
    return values


def _external(connection: str) -> str:
    name = env_name(connection)
    return os.environ.get(name, "").strip() or str(_dotenv().get(name) or "").strip()


def _git_root(path: Path):
    for folder in (path.parent, *path.parent.parents):
        if (folder / ".git").exists():
            return folder
    return None


def check_untracked(path: Path) -> None:
    """Refuse a .env that git could commit: inside a repository it must be ignored."""
    root = _git_root(path.resolve())
    if root is None:
        return
    git = shutil.which("git")
    if git is None:
        raise KeyFileError(f"{path} is inside a git repository and git is not available to check it is ignored. Keep the key for this session instead.")
    ignored = subprocess.run([git, "-C", str(root), "check-ignore", "-q", str(path.resolve())],
                             capture_output=True, timeout=10, env={k: v for k, v in os.environ.items() if not (k.endswith("_API_KEY") or k.startswith("COOPERATION_LAB_KEY_"))})
    if ignored.returncode != 0:
        raise KeyFileError(f"{path} is not ignored by git, so the key could be committed. Add .env to .gitignore, or keep the key for this session.")


def set_key(connection: str, key: str) -> None:
    with _lock:
        _entered[connection] = key
        _removed.discard(connection)


def save_to_env(connection: str, key: str) -> Path:
    """Write the key to the data folder's .env (this computer only, never git) and use it now."""
    path = env_file()
    check_untracked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _dotenv_set(str(path), env_name(connection), key, quote_mode="never")
    try:
        os.chmod(path, 0o600)  # owner only, where the OS supports it
    except OSError:
        pass
    set_key(connection, key)
    return path


def in_env_file(connection: str) -> bool:
    path = env_file()
    return path.is_file() and bool(dotenv_values(path).get(env_name(connection)))


def remove(connection: str, from_env_file: bool = False) -> None:
    """Forget the key for this session; with from_env_file, also delete its line from the data folder's .env."""
    if from_env_file and in_env_file(connection):
        _dotenv_unset(str(env_file()), env_name(connection))
    with _lock:
        _entered.pop(connection, None)
        _removed.add(connection)


def forget_session() -> None:
    """The user's "Forget all keys": entered keys gone, environment/.env keys hidden until the app restarts."""
    global _hide_external
    with _lock:
        _entered.clear()
        _hide_external = True


def forget_all() -> None:
    """Back to the start state (tests and restarts)."""
    global _hide_external
    with _lock:
        _entered.clear()
        _removed.clear()
        _hide_external = False


def get(connection: str) -> str:
    with _lock:
        if connection in _entered:
            return _entered[connection]
        if connection in _removed or _hide_external:
            return ""
    return _external(connection)


def source(connection: str) -> str | None:
    with _lock:
        if connection in _entered:
            return "entered"
        if connection in _removed or _hide_external:
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
    names = set(ENV_VARS.values()) | {n for n in (*os.environ, *_dotenv()) if n.startswith("COOPERATION_LAB_KEY_")}
    found += [v for v in (os.environ.get(n, "").strip() or str(_dotenv().get(n) or "").strip() for n in names) if v]
    return sorted({k for k in found if len(k) >= 4}, key=len, reverse=True)


def redact(text: str) -> str:
    """The text without any known key, or anything shaped like one."""
    if not text:
        return text
    for key in _known():
        text = text.replace(key, "[redacted key]")
    return _KEY_LIKE.sub("[redacted key]", text)
