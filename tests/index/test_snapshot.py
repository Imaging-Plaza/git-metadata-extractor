"""Tests for the read-only DuckDB snapshot publication (_snapshot.py).

The headline guarantee — proven by `test_snapshot_is_readable_from_a_separate_process`
— is that the `.ro.duckdb` snapshot can be opened read-only by a *separate
process* while the live DuckDB is held read-write, which is exactly what
404s today (the Hub's RO sniff vs GME's persistent RW lock).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import duckdb
import pytest

from src.index._snapshot import (
    delete_snapshot,
    publish_snapshot,
    snapshot_path_for,
)


def _make_live(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE models(repo_id TEXT, downloads BIGINT)")
    conn.execute("INSERT INTO models VALUES ('a/b', 10), ('c/d', 20)")
    conn.execute("CREATE TABLE chunks(chunk_id TEXT, text TEXT)")
    conn.execute("INSERT INTO chunks VALUES ('x', 'big embedding text')")
    return conn


def test_publish_copies_data_tables_and_skips_chunks(tmp_path: Path) -> None:
    live = tmp_path / "prov.duckdb"
    conn = _make_live(live)
    result = publish_snapshot(conn, live)
    conn.close()

    assert result["published"] is True
    snap = snapshot_path_for(live)
    assert snap == tmp_path / "prov.ro.duckdb"
    assert snap.exists()

    ro = duckdb.connect(str(snap), read_only=True)
    tables = {t for (t,) in ro.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'",
    ).fetchall()}
    assert "models" in tables
    assert "chunks" not in tables  # heavy embedding table skipped
    assert ro.execute("SELECT count(*) FROM models").fetchone()[0] == 2
    ro.close()


def test_snapshot_is_readable_from_a_separate_process(tmp_path: Path) -> None:
    """ACID test: a *different process* opens the snapshot RO while the live
    DuckDB is still held read-write here — the scenario that currently
    deadlocks the Hub on the live file."""
    live = tmp_path / "prov.duckdb"
    conn = _make_live(live)  # live RW handle stays open for the whole test
    publish_snapshot(conn, live)
    snap = snapshot_path_for(live)

    reader = textwrap.dedent(
        f"""
        import duckdb
        c = duckdb.connect({str(snap)!r}, read_only=True)
        print(c.execute("SELECT count(*) FROM models").fetchone()[0])
        """,
    )
    proc = subprocess.run(
        [sys.executable, "-c", reader],
        capture_output=True, text=True, timeout=60, check=False,
    )
    conn.close()
    assert proc.returncode == 0, f"separate-process RO open failed: {proc.stderr}"
    assert proc.stdout.strip() == "2"


def test_publish_is_atomic_no_leftover_tmp(tmp_path: Path) -> None:
    live = tmp_path / "prov.duckdb"
    conn = _make_live(live)
    publish_snapshot(conn, live)
    conn.close()
    snap = snapshot_path_for(live)
    assert not snap.with_name(snap.name + ".tmp").exists()


def test_disabled_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEX_DUCKDB_SNAPSHOT", "off")
    live = tmp_path / "prov.duckdb"
    conn = _make_live(live)
    result = publish_snapshot(conn, live)
    conn.close()
    assert result == {"enabled": False}
    assert not snapshot_path_for(live).exists()


def test_debounce_skips_fresh_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEX_SNAPSHOT_MIN_INTERVAL_SECONDS", "3600")
    live = tmp_path / "prov.duckdb"
    conn = _make_live(live)
    first = publish_snapshot(conn, live)
    second = publish_snapshot(conn, live)  # within the interval → skipped
    conn.close()
    assert first["published"] is True
    assert second["published"] is False
    assert second["reason"] == "debounced"


def test_delete_snapshot(tmp_path: Path) -> None:
    live = tmp_path / "prov.duckdb"
    conn = _make_live(live)
    publish_snapshot(conn, live)
    conn.close()
    assert snapshot_path_for(live).exists()
    assert delete_snapshot(live) is True
    assert not snapshot_path_for(live).exists()
    # idempotent
    assert delete_snapshot(live) is False
