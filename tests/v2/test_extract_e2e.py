from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from rdflib import Graph as RDFGraph

from src.v2.agents import ProviderSet
from src.v2.agents.models import AgentResult
from src.v2.api import v2_router
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages.models import ContextBundle
from src.v2.providers.base import GitHubProvider
from src.v2.providers.github_provider import RealGitHubProvider
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_orcid import MockORCIDProvider
from src.v2.providers.mock_ror import MockRORProvider

if TYPE_CHECKING:
    from src.v2.detection.models import GitHubURLClassification

HTTP_OK = 200
HTTP_UNPROCESSABLE_ENTITY = 422


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
    assert "entities" not in payload["output"]
    assert payload["output"]["root_entity"]

    entities = [
        entity
        for entity in [payload["output"]["root_entity"], *payload["output"]["related_entities"]]
        if isinstance(entity, dict)
    ]
    repository_entity = next(
        entity
        for entity in entities
        if entity.get("type") == "schema:SoftwareSourceCode"
    )
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
    entities = [
        entity
        for entity in [payload["output"]["root_entity"], *payload["output"]["related_entities"]]
        if isinstance(entity, dict)
    ]

    person = next(
        entity
        for entity in entities
        if entity.get("type") == "schema:Person"
        and entity.get("pulse:githubUsername") == "alice"
    )
    assert person["pulse:owns"] == ["owner-org/source-repo"]
    assert person["org:hasMembership"]

    organization_entities = [
        entity
        for entity in entities
        if entity.get("type") == "org:Organization"
    ]
    owner_org = next(
        entity
        for entity in organization_entities
        if entity.get("pulse:githubOrganizationHandle") == "owner-org"
    )
    assert owner_org["pulse:owns"] == ["owner-org/source-repo"]

    enriched_org = next(
        entity
        for entity in organization_entities
        if entity.get("pulse:githubOrganizationHandle") != "owner-org"
    )
    assert enriched_org.get("pulse:owns", []) == []


def test_extract_returns_422_when_root_entity_fails_strict_validation() -> None:
    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "owner/repo",
                    "metadata": {"owner": {"login": "owner", "type": "User"}},
                    "contributors": [],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={
                "id": "owner/repo",
                "type": "schema:SoftwareSourceCode",
                "shacl": "pulse:RepositoryShape",
                "identifiers": {
                    "pulse:githubRepositoryHandle": "owner/repo",
                    "schema:identifier": None,
                    "uuid": "a6bfb89f-f34d-4e67-84c9-1735d0524f24",
                },
                "idSource": "pulse:githubRepositoryHandle",
                "schema:name": "owner/repo",
                "pulse:githubRepositoryHandle": "owner/repo",
                "pulse:repositoryType": "pulse:Software",
                "pulse:discipline": ["wd:Q735"],
                "schema:author": [],
            },
        )

    async def _no_data_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> dict[str, Any]:
        return {}

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _no_data_agent,
            "org_agent": _no_data_agent,
            "article_agent": _no_data_agent,
            "membership_agent": _no_data_agent,
            "contribution_agent": _no_data_agent,
        },
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/owner/repo",
        params={"output_format": "json"},
    )

    assert status_code == HTTP_UNPROCESSABLE_ENTITY
    assert payload["error_type"] == "validation_error"
    assert payload["errors"]
    assert any(error["field"] == "schema:author" for error in payload["errors"])


def test_extract_excludes_invalid_non_root_entities_and_reports_collection() -> None:
    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "owner/repo",
                    "metadata": {"owner": {"login": "owner-org", "type": "Organization"}},
                    "contributors": [{"login": "alice"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={
                "id": "owner/repo",
                "type": "schema:SoftwareSourceCode",
                "shacl": "pulse:RepositoryShape",
                "identifiers": {
                    "pulse:githubRepositoryHandle": "owner/repo",
                    "schema:identifier": None,
                    "uuid": "f24d251f-c95b-45b7-b89e-b3306d7a42d6",
                },
                "idSource": "pulse:githubRepositoryHandle",
                "schema:name": "owner/repo",
                "pulse:githubRepositoryHandle": "owner/repo",
                "pulse:repositoryType": "pulse:Software",
                "pulse:discipline": ["wd:Q735"],
                "schema:author": ["alice"],
            },
        )

    async def _person_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={
                "id": "alice",
                "type": "schema:Person",
                "shacl": "pulse:PersonShape",
                "identifiers": {
                    "pulse:orcid": None,
                    "pulse:infosciencePersonIdentifier": None,
                    "pulse:githubUsername": "alice",
                    "uuid": "df0c2ce4-7a1f-4ef2-8af4-711b326c7ffd",
                },
                "idSource": "pulse:githubUsername",
                # Missing schema:name on purpose so strict validation excludes this non-root entity.
                "schema:email": "alice@example.org",
                "schema:url": "https://github.com/alice",
                "pulse:githubUsername": "alice",
                "pulse:orcidIdentifier": None,
                "pulse:infosciencePersonIdentifier": None,
                "org:hasMembership": [],
                "pulse:hasContribution": [],
                "pulse:owns": [],
            },
        )

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(
            data={
                "id": context["org_name"],
                "type": "org:Organization",
                "shacl": "pulse:OrganizationShape",
                "identifiers": {
                    "pulse:ror": None,
                    "pulse:infoscienceOrganizationIdentifier": None,
                    "pulse:githubOrganizationHandle": context["org_name"],
                    "uuid": "87d13d9a-f4ab-4f42-b5c5-4a558f1ddac5",
                },
                "idSource": "pulse:githubOrganizationHandle",
                "schema:name": context["org_name"],
                "schema:identifier": None,
                "pulse:githubOrganizationHandle": context["org_name"],
                "pulse:infoscienceOrganizationIdentifier": None,
                "pulse:OrganizationType": "pulse:SoftwareProject",
                "pulse:githubOrgFollowers": 1,
                "org:hasUnit": [],
                "org:unitOf": None,
                "pulse:owns": ["owner/repo"],
            },
        )

    async def _no_data_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> dict[str, Any]:
        return {}

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
            "article_agent": _no_data_agent,
            "membership_agent": _no_data_agent,
            "contribution_agent": _no_data_agent,
        },
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/owner/repo",
        params={"output_format": "json"},
    )

    assert status_code == HTTP_OK
    entities = [
        entity
        for entity in [payload["output"]["root_entity"], *payload["output"]["related_entities"]]
        if isinstance(entity, dict)
    ]
    assert any(entity.get("type") == "schema:SoftwareSourceCode" for entity in entities)
    assert not any(entity.get("type") == "schema:Person" for entity in entities)
    assert payload["output"]["excluded_entities"]
    assert any("Excluded person entity" in warning for warning in payload["warnings"])


def test_extract_jsonld_output_uses_stage_built_graph_contract() -> None:
    status_code, payload = _get_json(
        "/v2/extract/github.com/octocat/Hello-World",
        params={"output_format": "jsonld"},
    )

    assert status_code == HTTP_OK
    output = payload["output"]
    assert "@context" in output
    assert "@graph" in output
    assert isinstance(output["@graph"], list)
    assert "jsonld_build" in payload["stats"]["stages_completed"]

    for node in output["@graph"]:
        assert "@id" in node
        assert "@type" in node
        assert "id" not in node
        assert "type" not in node
        assert "shacl" not in node
        assert "identifiers" not in node
        assert "idSource" not in node

    graph = RDFGraph()
    graph.parse(data=json.dumps(output), format="json-ld")
    assert len(graph) > 0
