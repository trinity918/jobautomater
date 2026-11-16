"""SQLite storage. One file, WAL mode, a tiny helper layer instead of an ORM."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import DB_PATH

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    ats TEXT,
    company TEXT,
    title TEXT,
    location TEXT,
    remote INTEGER DEFAULT 0,
    url TEXT,
    apply_url TEXT,
    description TEXT,
    salary TEXT,
    posted_at REAL,
    first_seen REAL,
    last_seen REAL,
    score REAL,
    local_score REAL,
    llm_score REAL,
    reason TEXT,
    breakdown TEXT,
    status TEXT DEFAULT 'new',
    extra TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs(company);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT,
    started_at REAL,
    updated_at REAL,
    error TEXT,
    confirmation TEXT,
    answers TEXT,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_apps_job ON applications(job_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER,
    at REAL,
    level TEXT,
    step TEXT,
    message TEXT,
    screenshot TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_app ON events(application_id);

CREATE TABLE IF NOT EXISTS answers (
    label_norm TEXT PRIMARY KEY,
    label TEXT,
    answer TEXT,
    source TEXT,
    used_count INTEGER DEFAULT 0,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS llm_cache (
    prompt_hash TEXT PRIMARY KEY,
    response TEXT,
    tokens INTEGER,
    at REAL
);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(SCHEMA)
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(jobs)")}
        if "skills" not in cols:
            _conn.execute("ALTER TABLE jobs ADD COLUMN skills TEXT")
        _conn.commit()
    return _conn


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _lock:
        c = conn().execute(sql, tuple(params))
        conn().commit()
        return c


def executemany(sql: str, rows: list[tuple]) -> None:
    with _lock:
        conn().executemany(sql, rows)
        conn().commit()


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def kv_get(k: str, default: Any = None) -> Any:
    r = one("SELECT v FROM kv WHERE k=?", (k,))
    return json.loads(r["v"]) if r else default


def kv_set(k: str, v: Any) -> None:
    execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


def now() -> float:
    return time.time()
