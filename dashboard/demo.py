"""PUBLIC_DEMO: the public, scripted-only mode (Phase 3).

Off unless the environment sets PUBLIC_DEMO to 1, true or yes. Read on every
call, so the backend checks and the info endpoints can never disagree. When
on, the sandbox and the Agent Lab refuse every agent except the scripted one
before any agent is built, the connection probe does not exist, and error
responses carry no exception text or provider detail (server log only).

Resource limits, also demo-only (each caller checks enabled() first):
- START_LIMIT run starts per rolling START_WINDOW_S, shared by the sandbox and
  the Agent Lab and counted for the whole process. There is no per-client
  identity, so no header (X-Forwarded-For included) can buy more starts.
- RETAIN newest reports per kind in memory and on disk; LISTING_MAX per listing.
"""
from __future__ import annotations

import heapq
import math
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

ENV = "PUBLIC_DEMO"
SCRIPTED_ONLY = "The public demo runs scripted test agents only."
START_LIMIT, START_WINDOW_S = 20, 60.0
RETAIN = 100
LISTING_MAX = 20
REPORT_ID = re.compile(r"[0-9a-f]{12}")  # the only file names retention may delete


def enabled() -> bool:
    return os.environ.get(ENV, "").strip().lower() in ("1", "true", "yes")


def flags() -> dict:
    """What the page may know about the mode. Never keys, URLs or secrets."""
    on = enabled()
    return {"public_demo": on, "scripted_only": on}


def scrub(error: dict) -> dict:
    """An error as the API may show it: in the public demo, without its technical `detail`."""
    return {k: v for k, v in error.items() if k != "detail"} if enabled() else error


# ---------------------------------------------------------------- start limit

class RateLimited(Exception):
    """Too many runs started recently; retry_after is whole seconds (at least 1)."""

    def __init__(self, retry_after: int):
        super().__init__("Too many runs were started on this demo in the last minute. Try again shortly.")
        self.retry_after = retry_after


_starts: deque = deque()
_starts_lock = threading.Lock()


def take_start(now: float | None = None) -> None:
    """Record one run start, or raise RateLimited if the rolling window is full."""
    now = time.monotonic() if now is None else now
    with _starts_lock:
        while _starts and now - _starts[0] >= START_WINDOW_S:
            _starts.popleft()
        if len(_starts) >= START_LIMIT:
            raise RateLimited(max(1, math.ceil(START_WINDOW_S - (now - _starts[0]))))
        _starts.append(now)


# ---------------------------------------------------------------- retention

def prune_memory(store: dict, keep: int, active_id: str | None) -> None:
    """Drop the oldest entries (by .created) until at most `keep` remain; never the active one."""
    extra = len(store) - keep
    if extra > 0:
        for old in heapq.nsmallest(extra, (o for o in store.values() if o.id != active_id), key=lambda o: o.created):
            del store[old.id]


def _reports(folder: Path) -> list:
    """Report files in `folder`, newest first by (mtime, name). Other files are never touched."""
    def stamp(p):
        try:
            return p.stat().st_mtime, p.name
        except OSError:  # removed meanwhile
            return -1.0, p.name
    files = [p for p in folder.glob("*.json") if REPORT_ID.fullmatch(p.stem)] if folder.is_dir() else []
    return sorted(files, key=stamp, reverse=True)


def newest_reports(folder: Path, n: int) -> list:
    return _reports(folder)[:n]


def prune_disk(folder: Path, keep: int, active_id: str | None) -> None:
    """Delete report files beyond the newest `keep`; never the active one."""
    for p in _reports(folder)[keep:]:
        if p.stem != active_id:
            try:
                p.unlink()
            except OSError:
                pass
