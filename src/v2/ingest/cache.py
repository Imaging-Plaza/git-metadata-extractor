"""SQLite-backed cache for provider responses.

Cached on success, never on exception or `None`. Single TTL applied to every
entry; configure via `V2_PROVIDER_CACHE_TTL_DAYS` (default 30) and
`V2_PROVIDER_CACHE_PATH` (default `.cache/v2/providers.db`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_DAYS = 30
SECONDS_PER_DAY = 86_400

# Per-request cache-refresh ("backfill") toggle. When active, `get_or_set`
# skips the read and always recomputes — so a single extraction re-fetches
# providers and re-runs the pipeline with current code, then overwrites the
# stale entries — WITHOUT clearing the whole cache. Carried in a ContextVar so
# it scopes to one request's async task. Set it via `set_cache_refresh`.
_refresh_active: ContextVar[bool] = ContextVar("v2_cache_refresh", default=False)


def set_cache_refresh(enabled: bool) -> object:
    """Enable/disable cache-refresh for the current task; returns a reset token."""
    return _refresh_active.set(bool(enabled))


def reset_cache_refresh(token: object) -> None:
    _refresh_active.reset(token)  # type: ignore[arg-type]


def cache_refresh_active() -> bool:
    return _refresh_active.get()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_responses_expires ON responses(expires_at);
"""


class ProviderCache:
    """SQLite-backed key/value cache with a single TTL applied to every entry."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        default_ttl_seconds: float = DEFAULT_CACHE_TTL_DAYS * SECONDS_PER_DAY,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._default_ttl_seconds = default_ttl_seconds
        with self._connect() as conn, conn:
            # WAL is a database-level setting; once flipped it persists in the
            # file. Doing it here ensures fresh dbs are WAL from row #1.
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(_SCHEMA)
            conn.execute("DELETE FROM responses WHERE expires_at < ?;", (time.time(),))

    @property
    def default_ttl_seconds(self) -> float:
        return self._default_ttl_seconds

    def _connect(self) -> sqlite3.Connection:
        # Long lock timeout + WAL + relaxed fsync make concurrent writers
        # (e.g. parallel /extract requests) wait instead of erroring.
        conn = sqlite3.connect(str(self._db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    @staticmethod
    def make_key(provider: str, method: str, **kwargs: Any) -> str:
        payload = json.dumps(
            {"p": provider, "m": method, "a": kwargs},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM responses WHERE key = ?;",
                (key,),
            ).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if expires_at < time.time():
            return None
        return json.loads(value)

    def set(self, key: str, value: Any, *, ttl_seconds: float | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        with self._connect() as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO responses (key, value, expires_at) VALUES (?, ?, ?);",
                (key, json.dumps(value, default=str), time.time() + ttl),
            )

    def get_or_set(
        self,
        key: str,
        factory: Callable[[], Any],
        *,
        ttl_seconds: float | None = None,
        label: str | None = None,
    ) -> Any:
        if not _refresh_active.get():
            cached = self.get(key)
            if cached is not None:
                if label:
                    logger.info("provider cache hit: %s", label)
                return cached
        elif label:
            logger.info("provider cache refresh (bypass read): %s", label)
        value = factory()
        if value is not None:
            self.set(key, value, ttl_seconds=ttl_seconds)
        return value

    def clear(self) -> int:
        """Remove every cached entry. Returns the number of rows deleted."""

        with self._connect() as conn, conn:
            # `DELETE FROM <table>` with no WHERE triggers SQLite's
            # truncate optimization, which makes `cursor.rowcount` return
            # 0 regardless of how many rows were actually removed. Count
            # first so the caller (and the /v2/cache/clear endpoint) gets
            # a truthful number.
            count = conn.execute("SELECT COUNT(*) FROM responses;").fetchone()[0]
            conn.execute("DELETE FROM responses;")
            return int(count or 0)


__all__ = [
    "DEFAULT_CACHE_TTL_DAYS",
    "SECONDS_PER_DAY",
    "ProviderCache",
    "cache_refresh_active",
    "reset_cache_refresh",
    "set_cache_refresh",
]
