"""GET /v2/manifest — the federated store manifest over HTTP."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router

# Matches the token seeded by tests/v2/conftest.py's autouse env fixture.
TEST_API_TOKEN = "test-api-token"  # noqa: S105 — test fixture
_AUTH = {"Authorization": f"Bearer {TEST_API_TOKEN}"}

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403


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
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None)

    import asyncio

    return asyncio.run(_run())


_REQUIRED = {"name", "duckdb", "entity_types", "backend", "surface_as_source", "id_shape"}


def test_manifest_returns_all_stores() -> None:
    status, body = _get("/v2/manifest")
    assert status == HTTP_OK
    assert isinstance(body, list) and body
    for entry in body:
        assert _REQUIRED <= entry.keys()
        assert entry["duckdb"] == f"{entry['name']}.duckdb"


def test_manifest_includes_zenodo_communities_tile() -> None:
    _, body = _get("/v2/manifest")
    by = {e["name"]: e for e in body}
    assert by["zenodo_communities"]["backend"] == "duckdb"
    assert by["zenodo_communities"]["surface_as_source"] is True


def test_manifest_sources_filter() -> None:
    _, all_body = _get("/v2/manifest")
    _, src_body = _get("/v2/manifest?sources=true")
    src_names = {e["name"] for e in src_body}
    assert "zenodo_communities" in src_names              # allowlisted duckdb-only
    assert len(src_body) <= len(all_body)
    for e in src_body:
        assert e["backend"] == "vector" or e["surface_as_source"]


def test_manifest_requires_auth() -> None:
    status, _ = _get("/v2/manifest", auth=False)
    assert status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN)
