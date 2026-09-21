"""Run-level provenance: what code, spec, prompts and dependencies produced
a run. Everything here is read-only inspection; nothing affects behavior."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Identifies the frozen negotiation semantics (docs/spec.md §3).
PROTOCOL_ID = "alternating-offers/final-turn-deadline/v1"
DEPENDENCIES = ["pydantic", "anthropic", "openai", "python-dotenv", "pandas", "matplotlib"]


def _sha256(*chunks: bytes) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(c)
    return h.hexdigest()


def _text_bytes(path: Path) -> bytes:
    """File bytes with CRLF normalized, so fingerprints do not change with
    git's line-ending conversion between checkouts."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def prompt_hash() -> str:
    """Fingerprint of everything that shapes what models see: the prompt
    module source plus both structured-output schemas."""
    from src.agents.claude_agent import ACTION_TOOL
    from src.agents.openai_agent import RESPONSE_SCHEMA

    src = _text_bytes(ROOT / "src" / "agents" / "prompting.py")
    schemas = json.dumps([ACTION_TOOL, RESPONSE_SCHEMA], sort_keys=True).encode()
    return _sha256(src, schemas)


def spec_version() -> str:
    spec = ROOT / "docs" / "spec.md"
    return f"sha256:{_sha256(_text_bytes(spec))}" if spec.exists() else "unknown"


def code_version() -> str:
    """Git commit hash, suffixed '+dirty' if the working tree has changes;
    'unknown' if this is not a git checkout."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              check=True, timeout=10).stdout.strip()
    try:
        commit = git("rev-parse", "HEAD")
        return commit + ("+dirty" if git("status", "--porcelain") else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def dependency_versions() -> dict:
    versions = {"python": sys.version.split()[0], "platform": platform.platform()}
    for name in DEPENDENCIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def environment_params(num_categories: int) -> dict:
    """The environment-generation parameters in force (read from the modules
    that own them, so this can never drift from what generation uses)."""
    from src.environment import resources, valuations

    return {
        "category_names": resources.DEFAULT_CATEGORY_NAMES[:num_categories],
        "num_categories": num_categories,
        "min_qty": resources.DEFAULT_MIN_QTY,
        "max_qty": resources.DEFAULT_MAX_QTY,
        "total_points": valuations.TOTAL_POINTS,
        "dirichlet_alpha": valuations.DEFAULT_ALPHA,
        "seed_derivation": "sha256(f'{instance_seed}|{role}')[:8] (valuations); random.Random(instance_seed) (pool)",
    }
