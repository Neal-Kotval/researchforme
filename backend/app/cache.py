"""Tiny SQLite cache with TTL.

Two logical namespaces:
  - 'ingest'  : raw + extracted signals keyed by (source, query-hash)
  - 'report'  : full synthesized GapReport keyed by (area, sub_segments-hash)

Lets the UI re-run / reweight without re-fetching everything.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Optional

from .config import get_settings

_LOCK = threading.Lock()


def _key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class Cache:
    def __init__(self, path: Optional[str] = None, ttl_hours: Optional[int] = None):
        s = get_settings()
        self.path = path or s.cache_path
        self.ttl = (ttl_hours if ttl_hours is not None else s.cache_ttl_hours) * 3600
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init(self) -> None:
        with _LOCK, self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS cache (
                       ns    TEXT NOT NULL,
                       k     TEXT NOT NULL,
                       value TEXT NOT NULL,
                       ts    REAL NOT NULL,
                       PRIMARY KEY (ns, k)
                   )"""
            )

    def get(self, ns: str, *parts: Any, ttl: Optional[int] = None) -> Optional[Any]:
        k = _key(*parts)
        ttl = self.ttl if ttl is None else ttl
        with _LOCK, self._conn() as conn:
            row = conn.execute(
                "SELECT value, ts FROM cache WHERE ns=? AND k=?", (ns, k)
            ).fetchone()
        if not row:
            return None
        value, ts = row
        if ttl >= 0 and (time.time() - ts) > ttl:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def set(self, ns: str, value: Any, *parts: Any) -> None:
        k = _key(*parts)
        payload = json.dumps(value, default=str)
        with _LOCK, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (ns, k, value, ts) VALUES (?,?,?,?)",
                (ns, k, payload, time.time()),
            )

    def clear(self, ns: Optional[str] = None) -> None:
        with _LOCK, self._conn() as conn:
            if ns:
                conn.execute("DELETE FROM cache WHERE ns=?", (ns,))
            else:
                conn.execute("DELETE FROM cache")


_cache: Optional[Cache] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
