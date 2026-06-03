"""Read-only DuckDB snapshot publication for the index stores.

The GME serving process holds a persistent read-write DuckDB handle on
each ``<provider>.duckdb``. DuckDB allows N readers **or** 1 writer
(exclusive), not both — so a *separate* process (the Hub) that opens the
live file read-only to sniff the schema fails with a lock conflict, the
collection never registers, and queries 404.

``publish_snapshot`` breaks the contention by writing a read-only *copy*
to ``<provider>.ro.duckdb`` after each ingest. Writer and readers then
operate on **different files** → zero contention, even mid-ingest. The
Hub points at the ``.ro.duckdb`` snapshot; the live file stays owned
solely by GME (which reads it in-process via its own writer connection).

The snapshot is produced through the live (writer) connection itself:
``CHECKPOINT`` (fold the WAL), copy each data table into a fresh temp DB
via ``CREATE TABLE … AS SELECT …``, then atomically ``os.replace`` the
temp over the snapshot. The heavy ``chunks`` table (embedding
bookkeeping, never served) is skipped. Best-effort — never raises into
the calling ingest job.

Toggles:
  * ``INDEX_DUCKDB_SNAPSHOT`` (default on) — set falsey to disable.
  * ``INDEX_SNAPSHOT_MIN_INTERVAL_SECONDS`` (default 0) — debounce: skip
    if the existing snapshot is younger than this (avoids re-copying a
    multi-GB store on every single-item ingest).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

SNAPSHOT_SKIP_TABLES = frozenset({"chunks"})

_FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}
_ENV_FLAG = "INDEX_DUCKDB_SNAPSHOT"
_ENV_MIN_INTERVAL = "INDEX_SNAPSHOT_MIN_INTERVAL_SECONDS"


def snapshot_path_for(live_path: Path) -> Path:
    """Return the `.ro.duckdb` snapshot path beside the live DuckDB file."""
    live_path = Path(live_path)
    return live_path.with_name(f"{live_path.stem}.ro{live_path.suffix}")


def _enabled() -> bool:
    raw = os.getenv(_ENV_FLAG)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSE_ENV_VALUES


def _min_interval_seconds() -> float:
    raw = os.getenv(_ENV_MIN_INTERVAL)
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def publish_snapshot(
    conn: Any,
    live_path: Path,
    *,
    skip_tables: frozenset[str] = SNAPSHOT_SKIP_TABLES,
    force: bool = False,
) -> dict[str, Any]:
    """Publish a read-only snapshot of ``live_path``'s data tables.

    ``conn`` is the live (writer) connection to ``live_path``; the copy is
    made through it so it sees committed data. Best-effort: any failure is
    logged and returned, never raised. Returns a small status dict.

    ``force`` bypasses the min-interval debounce — use it for one-shot bulk
    mutations (e.g. a migration) that must publish immediately regardless of
    how recently a snapshot was written.
    """
    if not _enabled():
        return {"enabled": False}

    live_path = Path(live_path)
    snap = snapshot_path_for(live_path)

    interval = _min_interval_seconds()
    if not force and interval > 0 and snap.exists():
        try:
            if (time.time() - snap.stat().st_mtime) < interval:
                return {"enabled": True, "published": False, "reason": "debounced"}
        except OSError:
            pass

    tmp = snap.with_name(snap.name + ".tmp")
    try:
        if tmp.exists():
            tmp.unlink()
        conn.execute("CHECKPOINT")
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'",
        ).fetchall()
        tables = [t for (t,) in rows if t not in skip_tables]
        conn.execute(f"ATTACH '{tmp}' AS _snapshot")
        try:
            for table in tables:
                conn.execute(
                    f'CREATE TABLE _snapshot."{table}" AS SELECT * FROM "{table}"',
                )
        finally:
            conn.execute("DETACH _snapshot")
        os.replace(tmp, snap)
        LOGGER.info(
            "snapshot published: %s (%d table(s))", snap.name, len(tables),
        )
        return {
            "enabled": True,
            "published": True,
            "tables": len(tables),
            "path": str(snap),
        }
    except Exception as exc:  # noqa: BLE001 — snapshot is best-effort
        LOGGER.warning("snapshot publish failed for %s: %s", live_path, exc)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return {"enabled": True, "published": False, "error": str(exc)}


def delete_snapshot(live_path: Path) -> bool:
    """Remove the snapshot (+ any stale temp) beside ``live_path``. Used by
    the reset flow so a wiped provider doesn't keep serving stale data to
    the Hub. Returns True if anything was removed."""
    snap = snapshot_path_for(Path(live_path))
    removed = False
    for path in (snap, snap.with_name(snap.name + ".tmp")):
        try:
            if path.exists():
                path.unlink()
                removed = True
        except OSError as exc:
            LOGGER.warning("snapshot delete failed for %s: %s", path, exc)
    return removed


__all__ = [
    "delete_snapshot",
    "publish_snapshot",
    "snapshot_path_for",
]
