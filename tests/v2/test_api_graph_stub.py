from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router
from src.v2.models.contracts import V2GraphResponse

HTTP_OK = 200


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _get_json(path: str, params: list[tuple[str, str]] | None = None) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        test_app = _build_test_app()
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, params=params)
        return response.status_code, response.json()

    return asyncio.run(_run())


def test_graph_endpoint_returns_empty_graph_envelope() -> None:
    status_code, payload = _get_json("/v2/graph")

    assert status_code == HTTP_OK
    assert "@context" in payload["graph_jsonld"]
    assert payload["graph_jsonld"]["@graph"] == []
    assert payload["stats"]["entities_count"] == 0
    assert V2GraphResponse.model_validate(payload)


def test_graph_endpoint_accepts_source_url_filter() -> None:
    status_code, payload = _get_json(
        "/v2/graph",
        params=[("source_url", "https://github.com/owner/repo")],
    )

    assert status_code == HTTP_OK
    assert V2GraphResponse.model_validate(payload)


def test_graph_endpoint_accepts_repeated_entity_type_filter() -> None:
    status_code, payload = _get_json(
        "/v2/graph",
        params=[("entity_type", "Person"), ("entity_type", "Organization")],
    )

    assert status_code == HTTP_OK
    assert V2GraphResponse.model_validate(payload)
