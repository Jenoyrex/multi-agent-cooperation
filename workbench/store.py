"""Workbench persistence: one SQLite file (sandbox_runs/workbench.db by default),
separate from the research database in results/. Each saved object is a JSON
document; the schema checks that it is valid JSON and that a simulation status is
one of the known ones."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "sandbox_runs" / "workbench.db"  # tests point this at a temporary file
STATUSES = ("running", "completed", "cancelled", "failed", "interrupted")
TABLES = ("agents", "environments", "simulations")
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
    doc TEXT NOT NULL CHECK (json_valid(doc)));
CREATE TABLE IF NOT EXISTS environments (id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
    doc TEXT NOT NULL CHECK (json_valid(doc)));
CREATE TABLE IF NOT EXISTS simulations (id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
    doc TEXT NOT NULL CHECK (json_valid(doc)));
"""
_lock = threading.Lock()
_opened: set = set()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    if DB_PATH not in _opened:  # first use of this file in this process
        conn.executescript(SCHEMA)
        # A simulation left 'running' by a previous process can never finish now.
        conn.execute("UPDATE simulations SET status = 'interrupted', "
                     "doc = json_set(doc, '$.status', 'interrupted') WHERE status = 'running'")
        conn.commit()
        _opened.add(DB_PATH)
    return conn


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def put(table: str, doc: dict) -> dict:
    """Insert or replace by doc['id'] (a new id when missing)."""
    assert table in TABLES
    now = time.time()
    doc = {**doc, "id": doc.get("id") or new_id(), "updated": now}
    doc.setdefault("created", now)
    with _lock:
        conn = _conn()
        try:
            cols = "id, created, updated, doc" + (", status" if table == "simulations" else "")
            vals = [doc["id"], doc["created"], now, json.dumps(doc)] + ([doc["status"]] if table == "simulations" else [])
            conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({', '.join('?' * len(vals))})", vals)
            conn.commit()
        finally:
            conn.close()
    return doc


def get(table: str, id_: str) -> Optional[dict]:
    assert table in TABLES
    with _lock:
        conn = _conn()
        try:
            row = conn.execute(f"SELECT doc FROM {table} WHERE id = ?", (id_,)).fetchone()
        finally:
            conn.close()
    return json.loads(row[0]) if row else None


def all_(table: str) -> list[dict]:
    """Every document, newest first."""
    assert table in TABLES
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(f"SELECT doc FROM {table} ORDER BY created DESC").fetchall()
        finally:
            conn.close()
    return [json.loads(r[0]) for r in rows]


def delete(table: str, id_: str) -> bool:
    assert table in TABLES
    with _lock:
        conn = _conn()
        try:
            n = conn.execute(f"DELETE FROM {table} WHERE id = ?", (id_,)).rowcount
            conn.commit()
        finally:
            conn.close()
    return n > 0
