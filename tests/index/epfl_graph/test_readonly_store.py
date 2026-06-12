# tests/index/epfl_graph/test_readonly_store.py
"""Bug 01: epfl_graph store read-only mode. Extraction-time consumers must open
read-only with an identical config so many connections coexist in one process
without DuckDB's "different configuration than existing connections" error.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.index.epfl_graph.storage.duckdb_store import EpflGraphStore


def _make_db(tmp_path: Path) -> Path:
    p = tmp_path / "epfl_graph.duckdb"
    store = EpflGraphStore.open(p)  # read-write, bootstraps schema
    store.upsert_category({"category_id": "C1", "name": "root"}, {})
    store.close()
    return p


def test_open_readonly_refuses_writes(tmp_path: Path):
    store = EpflGraphStore.open_readonly(_make_db(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="read-only"):
            store.bootstrap()
        with pytest.raises(RuntimeError, match="read-only"):
            store.upsert_category({"category_id": "C2"}, {})
        # the underlying connection is genuinely read-only too
        con = store.connect()
        with pytest.raises(duckdb.Error):
            con.execute("CREATE TABLE t (x INTEGER)")
    finally:
        store.close()


def test_many_readonly_connections_coexist(tmp_path: Path):
    p = _make_db(tmp_path)
    a = EpflGraphStore.open_readonly(p)
    b = EpflGraphStore.open_readonly(p)
    raw = duckdb.connect(str(p), read_only=True, config={})
    try:
        for con in (a.connect(), b.connect(), raw):
            assert con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 1
    finally:
        a.close()
        b.close()
        raw.close()


def test_open_readonly_still_reads(tmp_path: Path):
    store = EpflGraphStore.open_readonly(_make_db(tmp_path))
    try:
        assert store.fetch_category("C1")["name"] == "root"
    finally:
        store.close()
