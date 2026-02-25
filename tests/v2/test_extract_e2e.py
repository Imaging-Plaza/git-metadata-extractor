from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.agents import ProviderSet
from src.v2.api import v2_router
from src.v2.providers.base import GitHubProvider
from src.v2.providers.github_provider import RealGitHubProvider
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_orcid import MockORCIDProvider
from src.v2.providers.mock_ror import MockRORProvider

HTTP_OK = 200


class _RepositoryModeScopeGitHubProvider(GitHubProvider):
    def get_repository(self, full_name: str) -> dict[str, Any]:
        return {
            "name": full_name.split("/", maxsplit=1)[-1],
            "full_name": full_name,
            "html_url": f"https://github.com/{full_name}",
            "owner": {"login": "owner-org", "type": "Organization"},
            "description": "Repository",
            "stargazers_count": 1,
            "forks_count": 0,
            "created_at": "2020-01-01T00:00:00Z",
            "license": {"spdx_id": "MIT"},
            "fork": False,
            "source": {"full_name": None},
            "topics": [],
        }

    def get_user(self, username: str) -> dict[str, Any]:
        return {
            "login": username,
            "name": "Alice Smith",
            "html_url": f"https://github.com/{username}",
            "company": "EPFL",
            "orcid": "0000-0002-1825-0097",
            "repositories": ["repo-a", "repo-b", "repo-c"],
        }

    def get_organization(self, org_name: str) -> dict[str, Any]:
        return {
            "login": org_name,
            "name": org_name,
            "followers": 42,
            "repositories": ["lib-a", "lib-b", "lib-c"],
        }

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        del full_name
        return [{"login": "alice"}]

    def get_languages(self, full_name: str) -> dict[str, int]:
        del full_name
        return {"Python": 10}


def _build_test_app(provider_set: ProviderSet | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    app.state.v2_provider_set = provider_set or ProviderSet(
        github=MockGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
        ror=MockRORProvider(),
    )
    return app


def _get_json_from_app(
    app: FastAPI,
    path: str,
    params: dict[str, str] | None = None,
) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, params=params)
        return response.status_code, response.json()

    return asyncio.run(_run())


def _get_json(path: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
    return _get_json_from_app(_build_test_app(), path, params=params)


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


def test_repository_extract_limits_github_ownership_expansion_but_keeps_enrichment() -> None:
    provider_set = ProviderSet(
        github=_RepositoryModeScopeGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
        ror=MockRORProvider(),
    )

    status_code, payload = _get_json_from_app(
        _build_test_app(provider_set),
        "/v2/extract/github.com/owner-org/source-repo",
        params={"output_format": "json"},
    )

    assert status_code == HTTP_OK
    entities = payload["output"]["entities"]

    person = entities["person_agent:alice"]
    assert person["pulse:owns"] == ["owner-org/source-repo"]
    assert person["org:hasMembership"]

    owner_org = entities["org_agent:owner-org"]
    assert owner_org["pulse:owns"] == ["owner-org/source-repo"]

    enriched_org = entities["org_agent:EPFL"]
    assert enriched_org.get("pulse:owns", []) == []
