from __future__ import annotations

import asyncio
from typing import Any

from httpx import ASGITransport, AsyncClient

from src.api import app as main_app
from src.api import index

HTTP_OK = 200


def _get_json(path: str) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path)
        return response.status_code, response.json()

    return asyncio.run(_run())


def test_main_app_serves_v2_extract_route() -> None:
    status_code, payload = _get_json("/v2/extract/github.com/owner/repo")

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "repository"


def test_main_app_serves_v2_graph_route() -> None:
    status_code, payload = _get_json("/v2/graph")

    assert status_code == HTTP_OK
    assert "@graph" in payload["graph_jsonld"]


def test_main_app_v1_welcome_still_available() -> None:
    route_paths = {route.path for route in main_app.routes}
    payload = index()

    assert "/" in route_paths
    assert "title" in payload


def test_main_app_keeps_v1_repository_jsonld_route_registered() -> None:
    route_paths = {route.path for route in main_app.routes}

    assert "/v1/repository/llm/json-ld/{full_path:path}" in route_paths
