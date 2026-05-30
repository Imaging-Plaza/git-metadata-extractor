"""Tests for `GET /v2/indices/freshness` + `POST /v2/indices/zenodo_communities/search`."""

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

HTTP_OK = 200

TEST_API_TOKEN = "test-api-token"  # noqa: S105
_AUTH_HEADERS = {"Authorization": f"Bearer {TEST_API_TOKEN}"}


class _FakeStore:
    """Stats-endpoint-compatible Store wrapper around a tmp DuckDB."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn


def _seed_stats_db(
    path: Path, *, count: int = 5, ingested_at: datetime | None = None,
) -> None:
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE rows (id INTEGER PRIMARY KEY, ingested_at TIMESTAMP)")
    ts = ingested_at or datetime.now(timezone.utc)
    for i in range(count):
        conn.execute("INSERT INTO rows VALUES (?, ?)", [i, ts])
    conn.close()


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _request(app: FastAPI, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.request(
                method, path, headers=_AUTH_HEADERS, **kwargs,
            )
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = resp.text
        return resp.status_code, body

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Freshness endpoint
# ---------------------------------------------------------------------------


def test_freshness_endpoint_rolls_up_per_provider(tmp_path: Path, monkeypatch):
    """One catalog populated, the rest unavailable → roll-up still works."""
    db = tmp_path / "github_repos.duckdb"
    yesterday = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
    _seed_stats_db(db, count=42, ingested_at=yesterday)

    # Stub fetch_store_for_stats: return our store for `github`, None for the
    # rest. Keeps the test offline — no real DuckDBs need to be on disk.
    from src.v2 import api as v2_api

    def _fake_fetch(provider: str, app_state: Any) -> Any | None:
        return _FakeStore(db) if provider == "github_repos" else None

    monkeypatch.setattr(v2_api, "fetch_store_for_stats", _fake_fetch)

    app = _build_test_app()
    code, body = _request(app, "GET", "/v2/indices/freshness")

    assert code == HTTP_OK
    assert "as_of" in body
    assert isinstance(body["catalogs"], list)
    # All supported providers appear.
    providers = {c["provider"] for c in body["catalogs"]}
    assert "github_repos" in providers and "ror" in providers and "zenodo_communities" in providers

    by_provider = {c["provider"]: c for c in body["catalogs"]}
    gh = by_provider["github_repos"]
    assert gh["count"] == 42
    assert gh["last_updated"] is not None
    assert gh["age_seconds"] > 0

    # Providers with no store available have count=0, no last_updated.
    ror = by_provider["ror"]
    assert ror["count"] == 0
    assert ror.get("last_updated") is None
    assert ror.get("age_seconds") is None

    # The oldest_* convenience fields point at the only catalog we seeded.
    assert body["oldest_provider"] == "github_repos"
    assert body["oldest_age_seconds"] >= gh["age_seconds"] - 0.001


def test_freshness_endpoint_handles_no_aged_catalogs(monkeypatch):
    """All catalogs empty → response is still well-formed, oldest fields null."""
    from src.v2 import api as v2_api

    monkeypatch.setattr(
        v2_api, "fetch_store_for_stats", lambda *_a, **_k: None,
    )

    app = _build_test_app()
    code, body = _request(app, "GET", "/v2/indices/freshness")

    assert code == HTTP_OK
    # `response_model_exclude_none=True` strips null fields from the body
    # — `oldest_*` may be absent rather than literal null. Either is fine.
    assert body.get("oldest_provider") is None
    assert body.get("oldest_age_seconds") is None
    assert all(c["count"] == 0 for c in body["catalogs"])


# ---------------------------------------------------------------------------
# Communities search endpoint
# ---------------------------------------------------------------------------


def _seed_communities_db(path: Path) -> None:
    """Seed `communities.duckdb` with three rows the search will hit."""
    schema = (
        Path(__file__).resolve().parents[2]
        / "src" / "index" / "zenodo_communities" / "storage" / "schema.sql"
    ).read_text(encoding="utf-8")
    conn = duckdb.connect(str(path))
    for stmt in [s.strip() for s in schema.split(";") if s.strip()]:
        conn.execute(stmt + ";")
    rows = [
        ("zenodo:epfl", "zenodo", "epfl", None, "EPFL", "Research at EPFL", None,
         "public", None, None, None, None, None, '["epfl", "research"]', "{}"),
        ("zenodo:ethz", "zenodo", "ethz", None, "ETHZ", "ETH Zürich datasets", None,
         "public", None, None, None, None, None, '["ethz", "data"]', "{}"),
        ("zenodo:cern", "zenodo", "cern", None, "CERN openlab",
         "Open data from CERN", None,
         "public", None, None, None, None, None, '["cern", "physics"]', "{}"),
    ]
    cols = (
        "community_id, source, source_slug, parent_org, title, description, url, "
        "visibility, created_at, updated_at, curator_names, member_count, "
        "record_count, keywords, raw"
    )
    placeholders = ", ".join("?" for _ in range(15))
    for row in rows:
        conn.execute(f"INSERT INTO communities ({cols}) VALUES ({placeholders})", list(row))
    conn.close()


def test_communities_search_title_hits_outrank_description_hits(
    tmp_path: Path, monkeypatch,
):
    """`EPFL` query: title hit on row 1 (score 3), description hit on row 2 (score 2)."""
    db = tmp_path / "communities.duckdb"
    _seed_communities_db(db)

    # Point the adapter at the tmp DB.
    from src.index.zenodo_communities import paths as zenodo_communities_paths

    monkeypatch.setattr(zenodo_communities_paths, "duckdb_path", lambda: db)

    app = _build_test_app()
    code, body = _request(
        app, "POST", "/v2/indices/zenodo_communities/search",
        json={"query": "EPFL", "top_k": 10},
    )

    assert code == HTTP_OK
    assert body["index_name"] == "zenodo_communities"
    titles = [h["payload"].get("title") for h in body["hits"]]
    assert titles[0] == "EPFL"  # title hit ranks first


def test_communities_search_falls_back_to_keywords(
    tmp_path: Path, monkeypatch,
):
    """`physics` query: only present in keywords JSON → row 3 (CERN openlab)."""
    db = tmp_path / "communities.duckdb"
    _seed_communities_db(db)

    from src.index.zenodo_communities import paths as zenodo_communities_paths

    monkeypatch.setattr(zenodo_communities_paths, "duckdb_path", lambda: db)

    app = _build_test_app()
    code, body = _request(
        app, "POST", "/v2/indices/zenodo_communities/search",
        json={"query": "physics", "top_k": 10},
    )

    assert code == HTTP_OK
    titles = [h["payload"].get("title") for h in body["hits"]]
    assert "CERN openlab" in titles


def test_communities_search_returns_empty_when_db_missing(
    tmp_path: Path, monkeypatch,
):
    """No `communities.duckdb` on disk → hits=[] + `extra.error` filled."""
    from src.index.zenodo_communities import paths as zenodo_communities_paths

    monkeypatch.setattr(
        zenodo_communities_paths,
        "duckdb_path",
        lambda: tmp_path / "does-not-exist.duckdb",
    )

    app = _build_test_app()
    code, body = _request(
        app, "POST", "/v2/indices/zenodo_communities/search",
        json={"query": "anything", "top_k": 5},
    )

    assert code == HTTP_OK
    assert body["hits"] == []
    assert "does not exist" in body.get("extra", {}).get("error", "")
