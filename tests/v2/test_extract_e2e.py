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
from src.v2.graph.store import GraphStore
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages.models import ContextBundle
from src.v2.ingest.providers.base import GitHubProvider, InfoscienceProvider
from src.v2.ingest.providers.github_provider import RealGitHubProvider
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.ingest.providers.mock_orcid import MockORCIDProvider
from src.v2.ingest.providers.mock_ror import MockRORProvider

if TYPE_CHECKING:
    from src.v2.ingest.detection.models import GitHubURLClassification

HTTP_OK = 200
HTTP_UNPROCESSABLE_ENTITY = 422
HTTP_BAD_GATEWAY = 502
HTTP_INTERNAL_SERVER_ERROR = 500
EXPECTED_JSON_ENTITY_BUCKETS = {
    "repositories",
    "persons",
    "organizations",
    "articles",
    "memberships",
    "contributions",
}
EXPECTED_STAGE_SEQUENCE_BY_DETECTED_TYPE = {
    "repository": [
        "context_gather",
        "repo_agent",
        "person_agents",
        "org_agents",
        "article_agents",
        "membership_agents",
        "contribution_agents",
        "permissive_validation",
        "reconciliation",
        "strict_validation",
        "output_assembly",
        "link_veracity",
        "jsonld_build",
        "shacl_gate",
        "graph_write",
    ],
    "user": [
        "context_gather",
        "person_agent",
        "repo_agents",
        "org_agents",
        "article_agents",
        "membership_agents",
        "contribution_agents",
        "permissive_validation",
        "reconciliation",
        "strict_validation",
        "output_assembly",
        "link_veracity",
        "jsonld_build",
        "shacl_gate",
        "graph_write",
    ],
    "organization": [
        "context_gather",
        "org_agent",
        "person_agents",
        "repo_agents",
        "article_agents",
        "membership_agents",
        "contribution_agents",
        "permissive_validation",
        "reconciliation",
        "strict_validation",
        "output_assembly",
        "link_veracity",
        "jsonld_build",
        "shacl_gate",
        "graph_write",
    ],
}


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


class _UnresolvedAuthorInfoscienceProvider(InfoscienceProvider):
    def __init__(self, *, unresolved_authors: list[str]) -> None:
        self._unresolved_authors = unresolved_authors

    def search_person(self, query: str) -> list[dict[str, Any]]:
        del query
        return []

    def search_orgunit(self, query: str) -> list[dict[str, Any]]:
        del query
        return []

    def search_publications(self, query: str) -> list[dict[str, Any]]:
        del query
        return [
            {
                "infosciencePublicationIdentifier": "pub-unresolved-1",
                "title": "Repository Author Mapping",
                "authors": ["alice", *self._unresolved_authors],
                "publicationDate": "2025-06-01",
                "doi": "10.9999/repo-author-mapping",
                "url": "https://infoscience.epfl.ch/entities/publication/pub-unresolved-1",
                "sourceOrganization": None,
            },
        ]


class _FailingGitHubProvider(GitHubProvider):
    def _raise(self) -> None:
        raise RuntimeError("unauthorized github token")

    def get_repository(self, full_name: str) -> dict[str, Any]:
        del full_name
        self._raise()
        return {}

    def get_user(self, username: str) -> dict[str, Any]:
        del username
        self._raise()
        return {}

    def get_organization(self, org_name: str) -> dict[str, Any]:
        del org_name
        self._raise()
        return {}

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        del full_name
        self._raise()
        return []

    def get_languages(self, full_name: str) -> dict[str, int]:
        del full_name
        self._raise()
        return {}


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
    assert payload["stats"]["stages_completed"] == EXPECTED_STAGE_SEQUENCE_BY_DETECTED_TYPE["repository"]
    assert "entities" not in payload["output"]
    assert payload["output"]["root_entity"]
    assert set(payload["output"]) == {
        "root_entity",
        "related_entities",
        "excluded_entities",
        "entities_by_type",
    }
    assert set(payload["output"]["entities_by_type"]) == EXPECTED_JSON_ENTITY_BUCKETS

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
    assert payload["stats"]["entities_count"] == len(entities)


def test_extract_returns_provider_error_when_required_github_provider_fails() -> None:
    provider_set = ProviderSet(
        github=_FailingGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
        ror=MockRORProvider(),
    )
    status_code, payload = _get_json_from_app(
        _build_test_app(provider_set),
        "/v2/extract/github.com/octocat/Hello-World",
        params={"output_format": "json"},
    )

    assert status_code == HTTP_BAD_GATEWAY
    assert payload["error_type"] == "provider_error"
    assert "Required provider 'github'" in payload["detail"]


def test_extract_uses_llm_runtime_default_when_unset(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("V2_AGENT_RUNTIME_DEFAULT", raising=False)
    llm_repo_calls = 0
    rule_repo_calls = 0

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            nonlocal llm_repo_calls
            llm_repo_calls += 1
            return AgentResult(
                data={
                    "id": "owner/repo",
                    "type": "schema:SoftwareSourceCode",
                    "shacl": "pulse:RepositoryShape",
                    "identifiers": {
                        "pulse:githubRepositoryHandle": "owner/repo",
                        "schema:citation": None,
                        "uuid": "f24d251f-c95b-45b7-b89e-b3306d7a42d6",
                    },
                    "idSource": "pulse:githubRepositoryHandle",
                    "schema:name": "repo",
                    "pulse:githubRepositoryHandle": "owner/repo",
                    "pulse:repositoryType": "pulse:Software",
                    "pulse:discipline": ["wd:Q735"],
                    "schema:author": ["alice"],
                },
            )

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
                    "contributors": [{"login": "alice", "type": "User"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _rule_repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_repo_calls
        rule_repo_calls += 1
        return AgentResult(data={"id": "rule-repo"})

    async def _no_data_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={})

    def _make_person_result(username: str) -> AgentResult:
        return AgentResult(
            data={
                "id": username,
                "type": "schema:Person",
                "shacl": "pulse:PersonShape",
                "identifiers": {
                    "pulse:orcid": None,
                    "pulse:infosciencePersonIdentifier": None,
                    "pulse:githubUsername": username,
                    "uuid": "11111111-1111-4111-8111-111111111111",
                },
                "idSource": "pulse:githubUsername",
                "schema:name": username,
                "schema:url": f"https://github.com/{username}",
                "pulse:githubUsername": username,
                "pulse:orcidIdentifier": None,
                "pulse:infosciencePersonIdentifier": None,
                "org:hasMembership": [],
                "pulse:hasContribution": [],
                "pulse:owns": [],
            },
        )

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        username = context["username"]
        return _make_person_result(username)

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            username = context.get("username") or context.get("github_username", "unknown")
            return _make_person_result(username)

    class _LLMOrganizationRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            org_name = context.get("org_name", "unknown-org")
            return AgentResult(
                data={
                    "id": org_name,
                    "type": "org:Organization",
                    "shacl": "pulse:OrganizationShape",
                    "identifiers": {
                        "pulse:ror": None,
                        "pulse:infoscienceOrganizationIdentifier": None,
                        "pulse:githubOrganizationHandle": org_name,
                        "uuid": "22222222-2222-4222-8222-222222222222",
                    },
                    "idSource": "pulse:githubOrganizationHandle",
                    "schema:name": org_name,
                    "pulse:githubOrganizationHandle": org_name,
                },
            )

    class _LLMNoDataRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={})

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_organization_agent=_LLMOrganizationRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        agent_runners={
            "repo_agent": _rule_repo_agent,
            "person_agent": _person_agent,
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

    assert status_code == HTTP_OK
    assert llm_repo_calls == 1
    assert rule_repo_calls == 0
    assert payload["output"]["root_entity"]["id"].endswith("/owner/repo")


def test_extract_llm_runtime_failure_does_not_fallback_to_rule_based_repository_agent() -> None:
    rule_repo_calls = 0

    class _FailingLLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            raise RuntimeError("llm repository failure")

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

    async def _rule_repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_repo_calls
        rule_repo_calls += 1
        return AgentResult(data={"id": "rule-repo"})

    async def _no_data_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={})

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_FailingLLMRepositoryRunner(),
        agent_runners={
            "repo_agent": _rule_repo_agent,
            "person_agent": _no_data_agent,
            "org_agent": _no_data_agent,
            "article_agent": _no_data_agent,
            "membership_agent": _no_data_agent,
            "contribution_agent": _no_data_agent,
        },
        retry_max_retries=1,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/owner/repo",
        params={"output_format": "json", "agent_runtime": "llm"},
    )

    assert status_code == HTTP_INTERNAL_SERVER_ERROR
    assert payload["error_type"] == "pipeline_error"
    assert "LLM repository runtime failed without fallback" in payload["detail"]
    assert rule_repo_calls == 0


def test_extract_user_and_org_llm_runtime_use_llm_repo_fanout_runner() -> None:
    llm_repo_calls = 0
    rule_repo_calls = 0

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            nonlocal llm_repo_calls
            llm_repo_calls += 1
            full_name = context.get("full_name", "owner/repo")
            return AgentResult(
                data={
                    "id": full_name,
                    "type": "schema:SoftwareSourceCode",
                    "shacl": "pulse:RepositoryShape",
                    "identifiers": {
                        "pulse:githubRepositoryHandle": full_name,
                        "schema:citation": None,
                        "uuid": "f24d251f-c95b-45b7-b89e-b3306d7a42d6",
                    },
                    "idSource": "pulse:githubRepositoryHandle",
                    "schema:name": full_name,
                    "pulse:githubRepositoryHandle": full_name,
                    "pulse:repositoryType": "pulse:Software",
                    "pulse:discipline": ["wd:Q735"],
                    "schema:author": [],
                },
            )

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            username = context.get("username") or context.get("github_username", "unknown")
            return AgentResult(
                data={
                    "id": username,
                    "type": "schema:Person",
                    "shacl": "pulse:PersonShape",
                    "identifiers": {
                        "pulse:orcid": None,
                        "pulse:infosciencePersonIdentifier": None,
                        "pulse:githubUsername": username,
                        "uuid": "11111111-1111-4111-8111-111111111111",
                    },
                    "idSource": "pulse:githubUsername",
                    "schema:name": username,
                    "schema:url": f"https://github.com/{username}",
                    "pulse:githubUsername": username,
                    "pulse:orcidIdentifier": None,
                    "pulse:infosciencePersonIdentifier": None,
                    "org:hasMembership": [],
                    "pulse:hasContribution": [],
                    "pulse:owns": [],
                },
            )

    class _LLMOrganizationRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            org_name = context.get("org_name", "unknown-org")
            return AgentResult(
                data={
                    "id": org_name,
                    "type": "org:Organization",
                    "shacl": "pulse:OrganizationShape",
                    "identifiers": {
                        "pulse:ror": None,
                        "pulse:infoscienceOrganizationIdentifier": None,
                        "pulse:githubOrganizationHandle": org_name,
                        "uuid": "22222222-2222-4222-8222-222222222222",
                    },
                    "idSource": "pulse:githubOrganizationHandle",
                    "schema:name": org_name,
                    "schema:identifier": None,
                    "pulse:githubOrganizationHandle": org_name,
                    "pulse:infoscienceOrganizationIdentifier": None,
                    "pulse:OrganizationType": "pulse:SoftwareProject",
                    "pulse:githubOrgFollowers": 1,
                    "org:hasUnit": [],
                    "org:unitOf": None,
                    "pulse:owns": [],
                },
            )

    class _LLMNoDataRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={})

    async def _context_gatherer(
        detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        if detected_type == "user":
            return ContextBundle(
                detected_type="user",
                context={
                    "user": {
                        "username": "alice",
                        "profile": {"login": "alice"},
                        "owned_repos": ["alice/repo-a"],
                        "repository_contexts": {
                            "alice/repo-a": {
                                "full_name": "alice/repo-a",
                                "metadata": {"full_name": "alice/repo-a"},
                                "contributors": [],
                                "languages": {"Python": 1},
                                "readme_content": "README",
                                "gimie_jsonld": {},
                            },
                        },
                        "orcid_data": None,
                    },
                },
            )

        return ContextBundle(
            detected_type="organization",
            context={
                "organization": {
                    "org_name": "example",
                    "profile": {"login": "example"},
                    "members": [],
                    "owned_repos": ["example/repo-a"],
                    "repository_contexts": {
                        "example/repo-a": {
                            "full_name": "example/repo-a",
                            "metadata": {"full_name": "example/repo-a"},
                            "contributors": [],
                            "languages": {"Python": 1},
                            "readme_content": "README",
                            "gimie_jsonld": {},
                        },
                    },
                },
            },
        )

    async def _rule_repo_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        nonlocal rule_repo_calls
        rule_repo_calls += 1
        return AgentResult(data={"id": "rule-repo"})

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_organization_agent=_LLMOrganizationRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        agent_runners={
            "repo_agent": _rule_repo_agent,
        },
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    user_status, _user_payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/alice",
        params={"output_format": "json", "agent_runtime": "llm"},
    )
    org_status, _org_payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/orgs/example",
        params={"output_format": "json", "agent_runtime": "llm"},
    )

    assert user_status == HTTP_OK
    assert org_status == HTTP_OK
    assert llm_repo_calls >= 2
    assert rule_repo_calls == 0


def test_extract_runs_link_veracity_stage_and_persists_intermediates(
    monkeypatch: Any,
) -> None:
    from src.v2.pipeline.stages import LinkVeracityStageResult

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(
                data={
                    "id": "owner/repo",
                    "type": "schema:SoftwareSourceCode",
                    "shacl": "pulse:RepositoryShape",
                    "identifiers": {
                        "pulse:githubRepositoryHandle": "owner/repo",
                        "schema:citation": None,
                        "uuid": "f24d251f-c95b-45b7-b89e-b3306d7a42d6",
                    },
                    "idSource": "pulse:githubRepositoryHandle",
                    "schema:name": "owner/repo",
                    "pulse:githubRepositoryHandle": "owner/repo",
                    "pulse:repositoryType": "pulse:Software",
                    "pulse:discipline": ["wd:Q735"],
                    "schema:author": ["owner"],
                    "schema:license": "https://spdx.org/licenses/MIT.html",
                },
            )

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            username = context.get("username", "owner")
            return AgentResult(
                data={
                    "id": username,
                    "type": "schema:Person",
                    "shacl": "pulse:PersonShape",
                    "identifiers": {
                        "pulse:orcid": None,
                        "pulse:infosciencePersonIdentifier": None,
                        "pulse:githubUsername": username,
                        "uuid": "11111111-1111-4111-8111-111111111111",
                    },
                    "idSource": "pulse:githubUsername",
                    "schema:name": username,
                    "schema:url": f"https://github.com/{username}",
                    "pulse:githubUsername": username,
                    "pulse:orcidIdentifier": None,
                    "pulse:infosciencePersonIdentifier": None,
                    "org:hasMembership": [],
                    "pulse:hasContribution": [],
                    "pulse:owns": [],
                },
            )

    class _LLMNoDataRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={})

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
                    "contributors": [{"login": "owner", "type": "User"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _fake_run_link_veracity_stage(**kwargs: Any) -> LinkVeracityStageResult:
        del kwargs
        return LinkVeracityStageResult(
            records=[
                {
                    "link": "https://spdx.org/licenses/MIT.html",
                    "status": "ok",
                    "relationship_supported": False,
                    "source_entity_id": "urn:git-metadata-extractor:entity:owner/repo",
                    "predicate": "schema:license",
                    "relationships": [],
                },
                {
                    "link": "https://github.com/owner/repo",
                    "status": "error",
                    "error": "timeout",
                    "source_entity_id": "urn:git-metadata-extractor:entity:owner/repo",
                    "predicate": "schema:url",
                    "relationships": [],
                },
            ],
            warnings=[
                "Link veracity unsupported relationship: link=https://spdx.org/licenses/MIT.html, source=urn:git-metadata-extractor:entity:owner/repo, predicate=schema:license",
                "Link veracity check failed: link=https://github.com/owner/repo, error=timeout",
            ],
            checked_count=2,
            supported_count=0,
            unsupported_count=1,
            failed_count=1,
        )

    monkeypatch.setattr("src.v2.api.run_link_veracity_stage", _fake_run_link_veracity_stage)

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_organization_agent=_LLMNoDataRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/owner/repo",
        params={
            "output_format": "jsonld",
            "agent_runtime": "llm",
            "include_intermediates": "true",
        },
    )

    assert status_code == HTTP_OK
    assert "link_veracity" in payload["stats"]["stages_completed"]
    assert any("Link veracity summary: checked=2, supported=0, unsupported=1, failed=1" in warning for warning in payload["warnings"])
    assert "@graph" in payload["output"]

    intermediates = payload.get("intermediates") or []
    link_veracity_intermediates = [
        envelope
        for envelope in intermediates
        if envelope.get("agent_name") == "link_veracity"
    ]
    assert len(link_veracity_intermediates) == 2
    reconciliation_debug_intermediates = [
        envelope
        for envelope in intermediates
        if envelope.get("agent_name") == "reconciliation_debug"
    ]
    assert len(reconciliation_debug_intermediates) == 1


def test_extract_llm_runtime_includes_dedup_and_critic_stages_and_intermediates(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("src.v2.api.DEFAULT_INTERMEDIATE_LIMIT", 100)
    from src.v2.pipeline.stages.models import LLMCriticStageResult, LLMDedupStageResult

    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(
                data={
                    "id": "owner/repo",
                    "type": "schema:SoftwareSourceCode",
                    "shacl": "pulse:RepositoryShape",
                    "identifiers": {
                        "pulse:githubRepositoryHandle": "owner/repo",
                        "schema:citation": None,
                        "uuid": "f24d251f-c95b-45b7-b89e-b3306d7a42d6",
                    },
                    "idSource": "pulse:githubRepositoryHandle",
                    "schema:name": "owner/repo",
                    "pulse:githubRepositoryHandle": "owner/repo",
                    "pulse:repositoryType": "pulse:Software",
                    "pulse:discipline": ["wd:Q735"],
                    "schema:author": ["owner"],
                },
            )

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            username = context.get("username", "owner")
            return AgentResult(
                data={
                    "id": username,
                    "type": "schema:Person",
                    "shacl": "pulse:PersonShape",
                    "identifiers": {
                        "pulse:orcid": None,
                        "pulse:infosciencePersonIdentifier": None,
                        "pulse:githubUsername": username,
                        "uuid": "11111111-1111-4111-8111-111111111111",
                    },
                    "idSource": "pulse:githubUsername",
                    "schema:name": username,
                    "schema:url": f"https://github.com/{username}",
                    "pulse:githubUsername": username,
                    "pulse:orcidIdentifier": None,
                    "pulse:infosciencePersonIdentifier": None,
                    "org:hasMembership": [],
                    "pulse:hasContribution": [],
                    "pulse:owns": [],
                },
            )

    class _LLMNoDataRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={})

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
                    "contributors": [{"login": "owner", "type": "User"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                    "gimie_jsonld": {"@id": "https://github.com/owner/repo"},
                },
            },
        )

    async def _fake_dedup_stage(**kwargs: Any) -> LLMDedupStageResult:
        return LLMDedupStageResult(
            typed_entity_buckets=kwargs["typed_entity_buckets"],
            candidate_clusters={
                "organizations": [],
                "persons": [],
                "repositories": [],
                "articles": [],
            },
            resolution={
                "accepted_clusters": {},
                "rejected_clusters": {},
                "remaps": {},
            },
        )

    async def _fake_critic_stage(**kwargs: Any) -> LLMCriticStageResult:
        return LLMCriticStageResult(reconciled=kwargs["reconciled"])

    monkeypatch.setattr("src.v2.api.run_llm_dedup_stage", _fake_dedup_stage)
    monkeypatch.setattr("src.v2.api.run_llm_critic_stage", _fake_critic_stage)

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_organization_agent=_LLMNoDataRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/owner/repo",
        params={
            "output_format": "json",
            "agent_runtime": "llm",
            "include_intermediates": "true",
        },
    )

    assert status_code == HTTP_OK
    completed = payload["stats"]["stages_completed"]
    assert "llm_dedup" in completed
    assert "llm_critic" in completed
    assert completed.index("llm_dedup") > completed.index("permissive_validation")
    assert completed.index("llm_critic") > completed.index("reconciliation")

    intermediates = payload.get("intermediates") or []
    intermediate_names = {envelope.get("agent_name") for envelope in intermediates}
    assert "llm_dedup_candidates" in intermediate_names
    assert "llm_dedup_resolution" in intermediate_names
    assert "llm_critic_decisions" in intermediate_names
    assert "llm_critic_applied" in intermediate_names


def test_extract_llm_dedup_and_critic_fail_open_with_warnings(
    monkeypatch: Any,
) -> None:
    class _LLMRepositoryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(
                data={
                    "id": "owner/repo",
                    "type": "schema:SoftwareSourceCode",
                    "shacl": "pulse:RepositoryShape",
                    "identifiers": {
                        "pulse:githubRepositoryHandle": "owner/repo",
                        "schema:citation": None,
                        "uuid": "f24d251f-c95b-45b7-b89e-b3306d7a42d6",
                    },
                    "idSource": "pulse:githubRepositoryHandle",
                    "schema:name": "owner/repo",
                    "pulse:githubRepositoryHandle": "owner/repo",
                    "pulse:repositoryType": "pulse:Software",
                    "pulse:discipline": ["wd:Q735"],
                    "schema:author": ["owner"],
                },
            )

    class _LLMNoDataRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={})

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            username = context.get("username", "owner")
            return AgentResult(
                data={
                    "id": username,
                    "type": "schema:Person",
                    "shacl": "pulse:PersonShape",
                    "identifiers": {
                        "pulse:orcid": None,
                        "pulse:infosciencePersonIdentifier": None,
                        "pulse:githubUsername": username,
                        "uuid": "11111111-1111-4111-8111-111111111111",
                    },
                    "idSource": "pulse:githubUsername",
                    "schema:name": username,
                    "schema:url": f"https://github.com/{username}",
                    "pulse:githubUsername": username,
                    "pulse:orcidIdentifier": None,
                    "pulse:infosciencePersonIdentifier": None,
                    "org:hasMembership": [],
                    "pulse:hasContribution": [],
                    "pulse:owns": [],
                },
            )

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
                    "contributors": [{"login": "owner", "type": "User"}],
                    "languages": {"Python": 1},
                    "readme_content": "README",
                },
            },
        )

    async def _failing_dedup_stage(**kwargs: Any) -> Any:
        del kwargs
        raise RuntimeError("boom dedup")

    async def _failing_critic_stage(**kwargs: Any) -> Any:
        del kwargs
        raise RuntimeError("boom critic")

    monkeypatch.setattr("src.v2.api.run_llm_dedup_stage", _failing_dedup_stage)
    monkeypatch.setattr("src.v2.api.run_llm_critic_stage", _failing_critic_stage)

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_repository_agent=_LLMRepositoryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_organization_agent=_LLMNoDataRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/owner/repo",
        params={"output_format": "json", "agent_runtime": "llm"},
    )

    assert status_code == HTTP_OK
    assert any("llm_dedup stage failed: boom dedup" in warning for warning in payload["warnings"])
    assert any("llm_critic stage failed: boom critic" in warning for warning in payload["warnings"])
    assert "llm_dedup" in payload["stats"]["stages_completed"]
    assert "llm_critic" in payload["stats"]["stages_completed"]


def test_extract_reconciles_org_identity_to_ror_for_memberships(monkeypatch: Any) -> None:
    monkeypatch.setattr("src.v2.api.DEFAULT_INTERMEDIATE_LIMIT", 100)
    infoscience_uuid = "95372c6b-7d45-432e-a84e-660c9fa54e05"
    infoscience_org_id = (
        "https://infoscience.epfl.ch/server/api/core/items/"
        f"{infoscience_uuid}"
    )

    async def _context_gatherer(
        _detected_type: str,
        _url_info: GitHubURLClassification,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="repository",
            context={
                "repository": {
                    "full_name": "sdsc-ordes/gimie",
                    "metadata": {"owner": {"login": "sdsc-ordes", "type": "Organization"}},
                    "contributors": [{"login": "alice", "type": "User"}],
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
                "id": "sdsc-ordes/gimie",
                "type": "schema:SoftwareSourceCode",
                "shacl": "pulse:RepositoryShape",
                "identifiers": {
                    "pulse:githubRepositoryHandle": "sdsc-ordes/gimie",
                    "schema:citation": None,
                    "uuid": "f24d251f-c95b-45b7-b89e-b3306d7a42d6",
                },
                "idSource": "pulse:githubRepositoryHandle",
                "schema:name": "gimie",
                "pulse:githubRepositoryHandle": "sdsc-ordes/gimie",
                "schema:author": ["alice"],
                "pulse:repositoryType": "pulse:Software",
                "pulse:discipline": ["wd:Q735"],
                "pulse:ownedBy": "sdsc-ordes",
            },
        )

    async def _person_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        username = context["username"]
        return AgentResult(
            data={
                "id": username,
                "type": "schema:Person",
                "shacl": "pulse:PersonShape",
                "identifiers": {
                    "pulse:orcid": None,
                    "pulse:infosciencePersonIdentifier": None,
                    "pulse:githubUsername": username,
                    "uuid": "11111111-1111-4111-8111-111111111111",
                },
                "idSource": "pulse:githubUsername",
                "schema:name": username,
                "schema:url": f"https://github.com/{username}",
                "pulse:githubUsername": username,
                "pulse:orcidIdentifier": None,
                "pulse:infosciencePersonIdentifier": None,
                "org:hasMembership": [f"{username}_{infoscience_org_id}"],
                "pulse:hasContribution": [],
                "pulse:owns": [],
                "affiliations": [infoscience_org_id],
            },
        )

    async def _org_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        org_name = context["org_name"]
        if org_name == "sdsc-ordes":
            return AgentResult(
                data={
                    "id": "https://ror.org/02hdt9m26",
                    "type": "org:Organization",
                    "shacl": "pulse:OrganizationShape",
                    "identifiers": {
                        "pulse:ror": "https://ror.org/02hdt9m26",
                        "pulse:infoscienceOrganizationIdentifier": None,
                        "pulse:githubOrganizationHandle": "sdsc-ordes",
                        "uuid": "22222222-2222-4222-8222-222222222222",
                    },
                    "idSource": "pulse:ror",
                    "schema:name": "Swiss Data Science Center",
                    "schema:identifier": "https://ror.org/02hdt9m26",
                    "pulse:githubOrganizationHandle": "sdsc-ordes",
                    "pulse:infoscienceOrganizationIdentifier": None,
                    "pulse:OrganizationType": "pulse:University",
                    "pulse:githubOrgFollowers": 24,
                    "org:hasUnit": [],
                    "org:unitOf": None,
                    "pulse:owns": [],
                },
            )
        return AgentResult(
            data={
                "id": infoscience_org_id,
                "type": "org:Organization",
                "shacl": "pulse:OrganizationShape",
                "identifiers": {
                    "pulse:ror": None,
                    "pulse:infoscienceOrganizationIdentifier": infoscience_uuid,
                    "pulse:githubOrganizationHandle": "sdsc-ordes",
                    "uuid": "33333333-3333-4333-8333-333333333333",
                },
                "idSource": "pulse:infoscienceOrganizationIdentifier",
                "schema:name": "Swiss Data Science Center",
                "schema:identifier": None,
                "pulse:githubOrganizationHandle": "sdsc-ordes",
                "pulse:infoscienceOrganizationIdentifier": infoscience_uuid,
                "pulse:OrganizationType": "pulse:OtherOrganizationType",
                "pulse:githubOrgFollowers": None,
                "org:hasUnit": [],
                "org:unitOf": None,
                "pulse:owns": [],
            },
        )

    async def _membership_agent(
        context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        seed = context["membership_seed"]
        membership_id = f"{seed}_{infoscience_org_id}"
        return AgentResult(
            data={
                "id": membership_id,
                "type": "org:Membership",
                "shacl": "pulse:MembershipShape",
                "identifiers": {
                    "pulse:composite": membership_id,
                    "uuid": "44444444-4444-4444-8444-444444444444",
                },
                "idSource": "pulse:composite",
                "org:organization": infoscience_org_id,
                "org:role": "Research Engineer",
                "time:hasBeginning": "2024-01-01",
                "time:hasEnd": None,
            },
        )

    async def _no_data_agent(
        _context: dict[str, Any],
        _providers: ProviderSet,
    ) -> AgentResult:
        return AgentResult(data={})

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        agent_runners={
            "repo_agent": _repo_agent,
            "person_agent": _person_agent,
            "org_agent": _org_agent,
            "article_agent": _no_data_agent,
            "membership_agent": _membership_agent,
            "contribution_agent": _no_data_agent,
        },
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/sdsc-ordes/gimie",
        params={
            "output_format": "json",
            "agent_runtime": "rule_based",
            "include_intermediates": "true",
        },
    )

    assert status_code == HTTP_OK
    organizations = payload["output"]["entities_by_type"]["organizations"]
    memberships = payload["output"]["entities_by_type"]["memberships"]

    assert not any(org["id"] == infoscience_org_id for org in organizations)
    assert all(
        membership["org:organization"] == "https://ror.org/02hdt9m26"
        for membership in memberships
    )

    intermediates = payload.get("intermediates") or []
    reconciliation_debug = next(
        envelope
        for envelope in intermediates
        if envelope.get("agent_name") == "reconciliation_debug"
    )
    assert reconciliation_debug["data"]["merged_group_count"] >= 1
    assert reconciliation_debug["data"]["org_remap_count"] >= 1


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
    assert person["pulse:owns"] == ["https://github.com/owner-org/source-repo"]
    assert person["org:hasMembership"]

    organization_entities = [
        entity
        for entity in entities
        if entity.get("type") == "org:Organization"
    ]
    repository = next(
        entity
        for entity in entities
        if entity.get("type") == "schema:SoftwareSourceCode"
        and entity.get("pulse:githubRepositoryHandle") == "owner-org/source-repo"
    )
    owner_org_id = repository["pulse:ownedBy"]
    owner_org = next(
        entity
        for entity in organization_entities
        if entity.get("id") == owner_org_id
    )
    assert owner_org["pulse:owns"] == ["https://github.com/owner-org/source-repo"]
    assert all(
        org.get("pulse:owns", []) == []
        for org in organization_entities
        if org.get("pulse:githubOrganizationHandle") is None
    )
    assert any(
        org.get("pulse:githubOrganizationHandle") == "owner-org"
        and org.get("pulse:owns") == ["https://github.com/owner-org/source-repo"]
        for org in organization_entities
    )


def test_extract_json_contract_stage_sequence_for_user_and_org() -> None:
    for path, detected_type in (
        ("/v2/extract/github.com/octocat", "user"),
        ("/v2/extract/github.com/orgs/github", "organization"),
    ):
        status_code, payload = _get_json(path, params={"output_format": "json"})

        assert status_code == HTTP_OK
        assert payload["detected_type"] == detected_type
        assert set(payload["output"]) == {
            "root_entity",
            "related_entities",
            "excluded_entities",
            "entities_by_type",
        }
        assert set(payload["output"]["entities_by_type"]) == EXPECTED_JSON_ENTITY_BUCKETS
        assert payload["stats"]["stages_completed"] == EXPECTED_STAGE_SEQUENCE_BY_DETECTED_TYPE[detected_type]


def test_extract_drops_unresolved_article_authors_without_creating_fallback_people() -> None:
    unresolved_authors = [f"Unresolved Author {index}" for index in range(1, 201)]
    provider_set = ProviderSet(
        github=_RepositoryModeScopeGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=_UnresolvedAuthorInfoscienceProvider(
            unresolved_authors=unresolved_authors,
        ),
        ror=MockRORProvider(),
    )

    status_code, payload = _get_json_from_app(
        _build_test_app(provider_set),
        "/v2/extract/github.com/owner-org/source-repo",
        params={"output_format": "json"},
    )

    assert status_code == HTTP_OK
    persons = payload["output"]["entities_by_type"]["persons"]
    assert len(persons) == 1
    assert not any(
        isinstance(person.get("schema:email"), str)
        and person["schema:email"].endswith("@example.org")
        for person in persons
    )
    assert any(
        "Dropped unresolved article author references for 200 name(s)" in warning
        for warning in payload["warnings"]
    )


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
                    "schema:citation": None,
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


def test_extract_excludes_invalid_non_root_entities_and_reports_collection(
    tmp_path,
    monkeypatch,
) -> None:
    graph_db = tmp_path / "extract_excludes_invalid_non_root_entities.db"
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(graph_db))

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
                    "schema:citation": None,
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

    included_entity_ids = {
        entity["id"]
        for entity in entities
        if isinstance(entity.get("id"), str)
    }
    excluded_entity_ids = {
        excluded.get("entity", {}).get("id")
        for excluded in payload["output"]["excluded_entities"]
        if isinstance(excluded, dict) and isinstance(excluded.get("entity"), dict)
    }

    store = GraphStore(str(graph_db))
    runs = store.get_runs_by_source(payload["source_url"])
    assert runs
    latest_run = runs[0]
    run_entity_ids = set(latest_run.stats.get("entity_ids", []))
    assert run_entity_ids == included_entity_ids
    assert run_entity_ids.isdisjoint(excluded_entity_ids)

    for entity_id in included_entity_ids:
        assert store.get_entity(entity_id) is not None
    for entity_id in excluded_entity_ids:
        if isinstance(entity_id, str):
            assert store.get_entity(entity_id) is None


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
