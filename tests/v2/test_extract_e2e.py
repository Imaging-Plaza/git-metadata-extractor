from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.agents import ProviderSet
from src.v2.api import v2_router
from src.v2.providers.github_provider import RealGitHubProvider
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_orcid import MockORCIDProvider
from src.v2.providers.mock_ror import MockRORProvider

HTTP_OK = 200


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    app.state.v2_provider_set = ProviderSet(
        github=MockGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
        ror=MockRORProvider(),
    )
    return app


def _get_json(path: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=_build_test_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, params=params)
        return response.status_code, response.json()

    return asyncio.run(_run())


def test_extract_endpoint_runs_pipeline_with_mock_providers(monkeypatch: Any) -> None:
    def _blocked_real_repository_call(self: RealGitHubProvider, full_name: str) -> dict[str, Any]:
        del self, full_name
        raise AssertionError

    monkeypatch.setattr(
        RealGitHubProvider,
        "get_repository",
        _blocked_real_repository_call,
    )

    status_code, payload = _get_json(
        "/v2/extract/github.com/octocat/Hello-World",
        params={"output_format": "json"},
    )

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "repository"
    assert payload["warnings"] == [] or isinstance(payload["warnings"], list)
    assert payload["stats"]["stages_completed"]

    repository_entity = payload["output"]["entities"]["repo_agent"]
    assert repository_entity["type"] == "schema:SoftwareSourceCode"
    assert repository_entity["pulse:githubRepositoryHandle"] == "octocat/Hello-World"

