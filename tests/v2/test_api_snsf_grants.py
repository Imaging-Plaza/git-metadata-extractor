"""GET /v2/indices/snsf/grants and GET /v2/indices/snsf/grants/facets — HTTP surface.

Tests verify:
- 200 + {total, results} from /grants with auth.
- 200 + dict from /grants/facets with auth.
- 401/403 without auth.
- Filtering via query params works (status=Completed).
- Empty store returns {total:0, results:[]} rather than 500.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router

TEST_API_TOKEN = "test-api-token"  # noqa: S105 — test fixture
_AUTH = {"Authorization": f"Bearer {TEST_API_TOKEN}"}

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403

_BASE_GRANT = "https://data.snf.ch/grants/grant/"
_G1 = f"{_BASE_GRANT}400001"
_G2 = f"{_BASE_GRANT}400002"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _get(path: str, *, auth: bool = True) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH if auth else {},
        ) as client:
            r = await client.get(path)
        ct = r.headers.get("content-type", "")
        return r.status_code, (r.json() if ct.startswith("application/json") else None)

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------


def test_grants_requires_auth() -> None:
    status, _ = _get("/v2/indices/snsf/grants", auth=False)
    assert status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN)


def test_grants_facets_requires_auth() -> None:
    status, _ = _get("/v2/indices/snsf/grants/facets", auth=False)
    assert status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN)


# ---------------------------------------------------------------------------
# Empty store — must not 500
# ---------------------------------------------------------------------------


def test_grants_empty_store_returns_200_with_zero_total(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Even if the snsf store file doesn't exist, endpoint returns 200."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "nonexistent"))
    status, body = _get("/v2/indices/snsf/grants")
    assert status == HTTP_OK
    assert isinstance(body, dict)
    assert body.get("total") == 0
    assert body.get("results") == []


def test_grants_facets_empty_store_returns_200_empty_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path / "nonexistent"))
    status, body = _get("/v2/indices/snsf/grants/facets")
    assert status == HTTP_OK
    assert isinstance(body, dict)


# ---------------------------------------------------------------------------
# Seeded store tests
# ---------------------------------------------------------------------------


def _seed_snsf(db_path: Path) -> None:
    """Seed a tiny SNSF store for HTTP-layer testing."""
    from src.index.snsf.facets import build_facets  # noqa: PLC0415
    from src.index.snsf.storage.duckdb_store import SnsfStore  # noqa: PLC0415

    s = SnsfStore.open(db_path)
    conn = s.connect()

    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G1, "HTTP Test Grant 1", "HTTP Test Grant 1 EN",
            "Abstract one.", "one; test",
            "ProjectFunding", "EPF Lausanne - EPFL", "Completed",
            "Biology", "Life Sciences", 2021,
            "2021-01-01", "2023-12-31", 400_000,
        ],
    )
    conn.execute(
        "INSERT INTO grants "
        "(grant_number, title, title_english, abstract, keywords, "
        " funding_instrument, research_institution, state, main_discipline, "
        " main_field_of_research, call_decision_year, start_date, end_date, amount_granted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            _G2, "HTTP Test Grant 2", "HTTP Test Grant 2 EN",
            "Abstract two.", "two; test",
            "Ambizione", "ETH Zurich", "Active",
            "Physics", "Natural Sciences", 2022,
            "2022-06-01", "2024-05-31", 200_000,
        ],
    )
    build_facets(s)
    s.close()


def _make_snsf_dir(tmp_path: Path) -> Path:
    """Create the SNSF duckdb directory structure and return INDEX_DATA_DIR."""
    snsf_duckdb_dir = tmp_path / "snsf" / "duckdb"
    snsf_duckdb_dir.mkdir(parents=True)
    db_path = snsf_duckdb_dir / "snsf.duckdb"
    _seed_snsf(db_path)
    return tmp_path  # INDEX_DATA_DIR


def test_grants_seeded_returns_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    index_data_dir = _make_snsf_dir(tmp_path)
    monkeypatch.setenv("INDEX_DATA_DIR", str(index_data_dir))
    status, body = _get("/v2/indices/snsf/grants")
    assert status == HTTP_OK
    assert isinstance(body, dict)
    assert "total" in body
    assert "results" in body
    assert body["total"] == 2  # noqa: PLR2004
    assert len(body["results"]) == 2  # noqa: PLR2004


def test_grants_filter_by_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    index_data_dir = _make_snsf_dir(tmp_path)
    monkeypatch.setenv("INDEX_DATA_DIR", str(index_data_dir))
    status, body = _get("/v2/indices/snsf/grants?status=Completed")
    assert status == HTTP_OK
    assert body["total"] == 1
    assert body["results"][0]["state"] == "Completed"


def test_grants_filter_multiple_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    index_data_dir = _make_snsf_dir(tmp_path)
    monkeypatch.setenv("INDEX_DATA_DIR", str(index_data_dir))
    status, body = _get("/v2/indices/snsf/grants?status=Completed&status=Active")
    assert status == HTTP_OK
    assert body["total"] == 2  # noqa: PLR2004


def test_grants_text_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    index_data_dir = _make_snsf_dir(tmp_path)
    monkeypatch.setenv("INDEX_DATA_DIR", str(index_data_dir))
    status, body = _get("/v2/indices/snsf/grants?q=Abstract+one")
    assert status == HTTP_OK
    assert body["total"] == 1


def test_grants_limit_offset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    index_data_dir = _make_snsf_dir(tmp_path)
    monkeypatch.setenv("INDEX_DATA_DIR", str(index_data_dir))
    status, body = _get("/v2/indices/snsf/grants?limit=1&offset=0")
    assert status == HTTP_OK
    assert body["total"] == 2  # noqa: PLR2004
    assert len(body["results"]) == 1


def test_grants_facets_seeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    index_data_dir = _make_snsf_dir(tmp_path)
    monkeypatch.setenv("INDEX_DATA_DIR", str(index_data_dir))
    status, body = _get("/v2/indices/snsf/grants/facets")
    assert status == HTTP_OK
    assert isinstance(body, dict)
    assert "funding_instrument" in body
    assert "state" in body


def test_grants_facets_filtered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    index_data_dir = _make_snsf_dir(tmp_path)
    monkeypatch.setenv("INDEX_DATA_DIR", str(index_data_dir))
    status, body = _get("/v2/indices/snsf/grants/facets?status=Completed")
    assert status == HTTP_OK
    assert isinstance(body, dict)
    # state facet should exclude "Completed" self-filter → still shows Active
    state_values = {item["value"] for item in body.get("state", [])}
    assert "Active" in state_values
