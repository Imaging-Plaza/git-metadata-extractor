"""`GET /v2/indices/{provider}/stats` — unblock external readers (#52).

External consumers (Open Pulse Hub) used to poll each `.duckdb` file with
`duckdb.connect(path, read_only=True)`. That fails the moment the GME holds
a write connection, because DuckDB's lock is per-process. The new endpoint
runs `SELECT COUNT(*)` and a `MAX(timestamp_col)` lookup on the GME's
already-open connection, so the file lock is never contested.

These tests stand up a tiny FastAPI app with a hand-crafted DuckDB file
pre-seeded on `app.state.v2_<provider>_resources`, then call the endpoint
through the ASGI transport (no network, no Qdrant, no GitHub).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router
from src.v2.indices import stats as stats_module
from src.v2.indices.stats import (
    IndexStatsResponse,
    UnknownIndexProviderError,
    collect_index_stats,
    fetch_store_for_stats,
)

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_SERVICE_UNAVAILABLE = 503

# Matches the value seeded by the `_isolate_v2_runtime_env` autouse
# fixture in `tests/v2/conftest.py`.
TEST_API_TOKEN = "test-api-token"  # noqa: S105 — test fixture
_AUTH_HEADERS = {"Authorization": f"Bearer {TEST_API_TOKEN}"}


class _FakeStore:
    """Just enough surface for `fetch_store_for_stats` callers.

    `app.state.v2_<provider>_resources` is a tuple — we shove this fake
    in the right tuple slot per provider so the stats handler reads the
    pre-seeded DuckDB file we set up below.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(str(self._db_path))
        return self._conn


def _build_test_db(
    tmp_path: Path,
    *,
    tables: dict[str, list[dict[str, Any]]],
    timestamp_column: str | None = None,
) -> Path:
    """Materialise a tiny DuckDB with one or more tables for stats to count."""
    path = tmp_path / "stats.duckdb"
    conn = duckdb.connect(str(path))
    for name, rows in tables.items():
        cols: list[tuple[str, str]] = [("id", "INTEGER")]
        if timestamp_column is not None:
            cols.append((timestamp_column, "TIMESTAMP"))
        col_defs = ", ".join(f'"{c}" {t}' for c, t in cols)
        conn.execute(f'CREATE TABLE "{name}" ({col_defs})')
        for row in rows:
            placeholders = ", ".join("?" for _ in cols)
            values = [row.get(c) for c, _ in cols]
            conn.execute(
                f'INSERT INTO "{name}" VALUES ({placeholders})',
                values,
            )
    conn.close()
    return path


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _get(app: FastAPI, path: str) -> tuple[int, Any]:
    """Call `path` through the ASGI transport and return (status, json)."""

    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(path, headers=_AUTH_HEADERS)
        try:
            body: Any = resp.json()
        except Exception:  # noqa: BLE001 — response with no JSON body
            body = resp.text
        return resp.status_code, body

    return asyncio.run(_run())


# ----------------------------------------------------------------------------
# collect_index_stats — pure schema introspection
# ----------------------------------------------------------------------------


def test_collect_index_stats_sums_counts_across_tables(tmp_path: Path) -> None:
    db = _build_test_db(
        tmp_path,
        tables={
            "datasets": [{"id": i} for i in range(3)],
            "models": [{"id": i} for i in range(2)],
            "spaces": [{"id": 1}],
        },
    )
    conn = duckdb.connect(str(db))
    try:
        stats = collect_index_stats("huggingface", conn)
    finally:
        conn.close()
    assert stats.provider == "huggingface"
    assert stats.count == 6
    assert stats.by_table == {"datasets": 3, "models": 2, "spaces": 1}
    assert stats.last_updated is None  # no timestamp column on this fixture


def test_collect_index_stats_picks_max_timestamp_across_tables(tmp_path: Path) -> None:
    older = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 5, 24, 10, 36, 50, tzinfo=timezone.utc)
    db = _build_test_db(
        tmp_path,
        tables={
            "repos": [
                {"id": 1, "ingested_at": older},
                {"id": 2, "ingested_at": newer},
            ],
        },
        timestamp_column="ingested_at",
    )
    conn = duckdb.connect(str(db))
    try:
        stats = collect_index_stats("github_repos", conn)
    finally:
        conn.close()
    assert stats.count == 2
    assert stats.last_updated == newer


def test_collect_index_stats_empty_catalog(tmp_path: Path) -> None:
    db = _build_test_db(tmp_path, tables={"repos": []}, timestamp_column="ingested_at")
    conn = duckdb.connect(str(db))
    try:
        stats = collect_index_stats("github_repos", conn)
    finally:
        conn.close()
    assert stats.count == 0
    assert stats.by_table == {"repos": 0}
    assert stats.last_updated is None


# ----------------------------------------------------------------------------
# fetch_store_for_stats — provider dispatch
# ----------------------------------------------------------------------------


def test_fetch_store_unknown_provider_raises():
    with pytest.raises(UnknownIndexProviderError):
        fetch_store_for_stats("doesnotexist", app_state=object())


# ----------------------------------------------------------------------------
# GET /v2/indices/{provider}/stats — end-to-end through the router
# ----------------------------------------------------------------------------


def test_stats_endpoint_returns_counts_for_github(tmp_path: Path) -> None:
    db = _build_test_db(
        tmp_path,
        tables={
            "repos": [
                {"id": 1, "ingested_at": datetime(2026, 5, 13, tzinfo=timezone.utc)},
                {"id": 2, "ingested_at": datetime(2026, 5, 24, tzinfo=timezone.utc)},
            ],
        },
        timestamp_column="ingested_at",
    )
    app = _build_test_app()
    # github resources tuple is (config, store, client) — only the store
    # is read by the stats path, so the other slots can be plain Nones.
    app.state.v2_github_repos_resources = (None, _FakeStore(db), None)

    status_code, body = _get(app, "/v2/indices/github_repos/stats")

    assert status_code == HTTP_OK
    parsed = IndexStatsResponse.model_validate(body)
    assert parsed.provider == "github_repos"
    assert parsed.count == 2
    assert parsed.by_table == {"repos": 2}
    assert parsed.last_updated == datetime(2026, 5, 24, tzinfo=timezone.utc)


def test_stats_endpoint_503_when_resources_unavailable(monkeypatch) -> None:
    app = _build_test_app()
    # Force the resources getter to return None so we deterministically hit
    # the "unavailable" branch, regardless of whether a YAML/Qdrant happens
    # to be reachable in the test environment. The handler imports the name
    # into src.v2.api's namespace, so we patch it there.
    from src.v2 import api as v2_api

    monkeypatch.setattr(
        v2_api,
        "fetch_store_for_stats",
        lambda provider, app_state: None,
    )

    status_code, body = _get(app, "/v2/indices/huggingface/stats")

    assert status_code == HTTP_SERVICE_UNAVAILABLE
    assert "unavailable" in body["detail"]


def test_stats_endpoint_404_for_unknown_provider() -> None:
    app = _build_test_app()

    status_code, body = _get(app, "/v2/indices/totally-fake/stats")

    assert status_code == HTTP_NOT_FOUND
    assert "unknown index provider" in body["detail"]
