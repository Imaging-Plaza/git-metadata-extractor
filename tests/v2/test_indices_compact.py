"""Tests for `src/v2/indices/compact.py` — DuckDB EXPORT/IMPORT round-trip."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.v2.indices.compact import (
    CompactResult,
    close_cached_resources_for,
    compact_all_indexes,
    compact_duckdb,
)


def _seed_duckdb(path: Path, *, rows: int = 100) -> None:
    """Seed a DuckDB with one table + repeated upsert churn so there's
    something for the EXPORT/IMPORT cycle to reclaim."""
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, payload TEXT)")
    for _ in range(5):
        # Repeated inserts + deletes accumulate tombstones DuckDB only
        # reclaims via EXPORT/IMPORT round-trip.
        conn.execute(
            f"INSERT OR REPLACE INTO widgets VALUES "
            f"{', '.join(f'({i}, repeat(chr(65 + ({i} % 26)), 4096))' for i in range(rows))}",
        )
        if rows > 50:
            conn.execute(f"DELETE FROM widgets WHERE id >= {rows // 2}")
            conn.execute(
                f"INSERT INTO widgets VALUES "
                f"{', '.join(f'({i}, repeat(chr(90 - ({i} % 26)), 4096))' for i in range(rows // 2, rows))}",
            )
    conn.close()


def test_compact_duckdb_returns_metrics_and_preserves_data(tmp_path: Path):
    db_path = tmp_path / "widgets.duckdb"
    _seed_duckdb(db_path, rows=200)

    pre_bytes = db_path.stat().st_size
    pre_count = duckdb.connect(str(db_path), read_only=True).execute(
        "SELECT COUNT(*) FROM widgets",
    ).fetchone()[0]

    result = compact_duckdb("test_widgets", db_path)

    # File still exists, .bak / .compacting siblings are gone.
    assert db_path.exists()
    assert not (db_path.with_name(db_path.name + ".bak")).exists()
    assert not (db_path.with_name(db_path.name + ".compacting")).exists()

    # Row count preserved end-to-end.
    post_count = duckdb.connect(str(db_path), read_only=True).execute(
        "SELECT COUNT(*) FROM widgets",
    ).fetchone()[0]
    assert post_count == pre_count

    # Metrics make sense.
    assert isinstance(result, CompactResult)
    assert result.provider == "test_widgets"
    assert result.db_path == str(db_path)
    assert result.bytes_before == pre_bytes
    assert result.bytes_after == db_path.stat().st_size
    assert result.reclaimed_bytes == result.bytes_before - result.bytes_after
    assert 0.0 <= result.compression_ratio <= 1.5  # usually < 1, but DBs can grow if tiny
    assert result.table_count == 1
    assert result.elapsed_seconds >= 0


def test_compact_duckdb_raises_for_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        compact_duckdb("nope", tmp_path / "does-not-exist.duckdb")


def test_compact_all_indexes_walks_data_root(tmp_path: Path):
    """`compact_all_indexes(<dir>)` finds every *.duckdb under the dir."""
    a = tmp_path / "provider_a" / "duckdb"
    a.mkdir(parents=True)
    _seed_duckdb(a / "a.duckdb", rows=50)
    b = tmp_path / "provider_b" / "duckdb"
    b.mkdir(parents=True)
    _seed_duckdb(b / "b.duckdb", rows=50)

    results = compact_all_indexes(tmp_path, verbose=False)

    providers = sorted(r.provider for r in results)
    assert providers == ["provider_a", "provider_b"]
    for r in results:
        assert r.bytes_after > 0  # file still exists
        assert r.table_count == 1


def test_compact_all_indexes_raises_on_missing_root(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        compact_all_indexes(tmp_path / "missing")


def test_close_cached_resources_for_unsets_attr_and_calls_close():
    """The endpoint relies on this to release the file lock before compaction."""
    class _Closeable:
        closed = False

        def close(self) -> None:
            self.closed = True

    class _AppState:
        pass

    app_state = _AppState()
    closeable = _Closeable()
    # Tuple shape (mirrors `app_state.v2_<provider>_resources`).
    app_state.v2_github_repos_resources = (None, closeable, None)

    close_cached_resources_for("github_repos", app_state)

    assert closeable.closed is True
    assert app_state.v2_github_repos_resources is None


def test_close_cached_resources_for_handles_unknown_provider_silently():
    """Unknown providers are a no-op — endpoint already 404s elsewhere."""
    class _AppState:
        pass

    close_cached_resources_for("not-a-provider", _AppState())  # should not raise


def test_close_cached_resources_for_single_store_attr():
    """CLI-managed catalogs cache a single Store on `app_state.v2_<name>_store`."""
    class _Closeable:
        closed = False

        def close(self) -> None:
            self.closed = True

    class _AppState:
        pass

    app_state = _AppState()
    closeable = _Closeable()
    app_state.v2_ror_store = closeable

    close_cached_resources_for("ror", app_state)

    assert closeable.closed is True
    assert app_state.v2_ror_store is None
