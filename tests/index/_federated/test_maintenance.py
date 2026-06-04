"""Tests for the cross-store DuckDB maintenance driver."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.index._federated import maintenance
from src.index._snapshot import snapshot_path_for


def _make_store(root: Path, name: str, rows: int) -> Path:
    """Create a live store at <root>/<name>/duckdb/<name>.duckdb with `rows`."""
    d = root / name / "duckdb"
    d.mkdir(parents=True)
    path = d / f"{name}.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE items (id INTEGER, label TEXT)")
    con.executemany(
        "INSERT INTO items VALUES (?, ?)",
        [(i, f"row-{i}") for i in range(rows)],
    )
    con.close()
    return path


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INDEX_DUCKDB_SNAPSHOT", "1")
    monkeypatch.setenv("INDEX_SNAPSHOT_MIN_INTERVAL_SECONDS", "0")
    _make_store(tmp_path, "alpha_store", 3)
    _make_store(tmp_path, "beta_store", 5)
    return tmp_path


def test_discover_finds_live_stores_only(data_root: Path) -> None:
    stores = dict(maintenance.discover_stores())
    assert set(stores) == {"alpha_store", "beta_store"}
    # No .ro/.tmp leakage and the path is the live file.
    assert stores["alpha_store"].name == "alpha_store.duckdb"


def test_discover_store_filter(data_root: Path) -> None:
    stores = maintenance.discover_stores(only="beta_store")
    assert [n for n, _ in stores] == ["beta_store"]


def test_check_reports_counts_without_writing(data_root: Path) -> None:
    out = maintenance.run(check=True)
    assert out["mode"] == "check"
    by = {s["store"]: s for s in out["stores"]}
    assert by["alpha_store"]["tables"]["items"] == 3
    assert by["beta_store"]["tables"]["items"] == 5
    assert by["alpha_store"]["live_bytes"] > 0
    # --check must not publish snapshots.
    assert by["alpha_store"]["snapshot_present"] is False
    assert not snapshot_path_for(dict(maintenance.discover_stores())["alpha_store"]).exists()


def test_optimize_publishes_compacted_snapshots(data_root: Path) -> None:
    out = maintenance.run()
    assert out["mode"] == "optimize"
    by = {s["store"]: s for s in out["stores"]}
    for name in ("alpha_store", "beta_store"):
        assert by[name]["result"]["published"] is True
        snap = snapshot_path_for(dict(maintenance.discover_stores())[name])
        assert snap.exists()
        # Snapshot is a queryable copy with the same rows.
        con = duckdb.connect(str(snap), read_only=True)
        try:
            n = con.execute("SELECT count(*) FROM items").fetchone()[0]
        finally:
            con.close()
        assert n == (3 if name == "alpha_store" else 5)


def test_run_empty_root_is_graceful(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "empty"))
    out = maintenance.run()
    assert out["stores"] == []
    assert "no live DuckDB stores" in out["note"]
