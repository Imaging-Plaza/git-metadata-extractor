from __future__ import annotations

import asyncio
from typing import Any

from httpx import ASGITransport, AsyncClient

from src.api import app as main_app
from src.api import index
from src.v2.agents import ProviderSet
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.ingest.providers.mock_orcid import MockORCIDProvider
from src.v2.ingest.providers.mock_ror import MockRORProvider

HTTP_OK = 200
# The v2 conftest `_isolate_v2_runtime_env` autouse fixture sets
# `API_TOKEN=test-api-token` so `verify_token` (HTTPBearer) is active.
# Send the matching Bearer header so v2 routes don't reject the request
# with 401 before they reach the route handler.
_AUTH_HEADERS = {"Authorization": "Bearer test-api-token"}


def _get_json(path: str) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=main_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, headers=_AUTH_HEADERS)
        return response.status_code, response.json()

    return asyncio.run(_run())


def test_main_app_serves_v2_extract_route() -> None:
    main_app.state.v2_provider_set = ProviderSet(
        github=MockGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
        ror=MockRORProvider(),
    )
    status_code, payload = _get_json("/v2/extract/github.com/octocat/Hello-World")

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "repository"


def test_main_app_welcome_available() -> None:
    route_paths = {route.path for route in main_app.routes}
    payload = index()

    assert "/" in route_paths
    assert "title" in payload


def test_main_app_serves_no_v1_routes() -> None:
    # The v1 API was retired in 3.0.0 — nothing may mount /v1 again.
    route_paths = {route.path for route in main_app.routes}

    assert not any(path.startswith("/v1") for path in route_paths)
