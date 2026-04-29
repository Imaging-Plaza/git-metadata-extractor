from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.agents import ProviderSet
from src.v2.agents.models import AgentResult
from src.v2.api import v2_router
from src.v2.api_models.contracts import V2ExtractResponse
from src.v2.ingest.cache import ProviderCache
from src.v2.pipeline import PipelineOrchestrator
from src.v2.pipeline.stages.models import ContextBundle
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.ingest.providers.mock_orcid import MockORCIDProvider
from src.v2.ingest.providers.mock_ror import MockRORProvider

HTTP_OK = 200
HTTP_UNPROCESSABLE_ENTITY = 422


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
        test_app = _build_test_app()
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, params=params)
        return response.status_code, response.json()

    return asyncio.run(_run())


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


def _post_json(path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        test_app = _build_test_app()
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(path, json=payload)
        return response.status_code, response.json()

    return asyncio.run(_run())


def _post_json_from_app(
    app: FastAPI,
    path: str,
    payload: dict[str, Any],
) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(path, json=payload)
        return response.status_code, response.json()

    return asyncio.run(_run())


def test_extract_repository_url_returns_detected_repository() -> None:
    status_code, payload = _get_json("/v2/extract/github.com/octocat/Hello-World")

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "repository"
    assert V2ExtractResponse.model_validate(payload)


def test_extract_user_url_returns_detected_user() -> None:
    status_code, payload = _get_json("/v2/extract/github.com/octocat")

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "user"
    assert V2ExtractResponse.model_validate(payload)


def test_extract_post_repository_url_returns_detected_repository() -> None:
    status_code, payload = _post_json(
        "/v2/extract",
        {"source_url": "github.com/octocat/Hello-World"},
    )

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "repository"
    assert V2ExtractResponse.model_validate(payload)


def test_extract_unsupported_issue_url_returns_typed_422() -> None:
    status_code, payload = _get_json("/v2/extract/github.com/owner/repo/issues/1")

    assert status_code == HTTP_UNPROCESSABLE_ENTITY
    assert payload["error_type"] == "unsupported_url"
    assert payload["detected_path_kind"] == "issues"


def test_extract_accepts_output_format_json() -> None:
    status_code, payload = _get_json(
        "/v2/extract/github.com/octocat/Hello-World",
        params={"output_format": "json"},
    )

    assert status_code == HTTP_OK
    assert payload["output_format"] == "json"


def test_extract_rejects_invalid_output_format() -> None:
    status_code, payload = _get_json(
        "/v2/extract/github.com/octocat/Hello-World",
        params={"output_format": "xml"},
    )

    assert status_code == HTTP_UNPROCESSABLE_ENTITY
    assert "detail" in payload


def test_extract_accepts_agent_runtime_llm_for_user_routes() -> None:
    async def _context_gatherer(
        _detected_type: str,
        _url_info: Any,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="user",
            context={
                "user": {
                    "username": "octocat",
                    "profile": {"login": "octocat"},
                    "owned_repos": [],
                    "orcid_data": None,
                },
            },
        )

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del providers
            username = context.get("username", "octocat")
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

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_person_agent=_LLMPersonRunner(),
        llm_repository_agent=_LLMNoDataRunner(),
        llm_organization_agent=_LLMNoDataRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/octocat",
        params={"agent_runtime": "llm"},
    )

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "user"
    assert V2ExtractResponse.model_validate(payload)


def test_extract_rejects_invalid_agent_runtime() -> None:
    status_code, payload = _get_json(
        "/v2/extract/github.com/octocat/Hello-World",
        params={"agent_runtime": "hybrid"},
    )

    assert status_code == HTTP_UNPROCESSABLE_ENTITY
    assert "detail" in payload


def test_extract_can_include_compiled_context_summary_in_response() -> None:
    class _SummaryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={"summary_markdown": "# Compiled Context\n- Key signal"})

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(
                data={
                    "id": "octocat",
                    "type": "schema:Person",
                    "shacl": "pulse:PersonShape",
                    "identifiers": {
                        "pulse:orcid": None,
                        "pulse:infosciencePersonIdentifier": None,
                        "pulse:githubUsername": "octocat",
                        "uuid": "11111111-1111-4111-8111-111111111111",
                    },
                    "idSource": "pulse:githubUsername",
                    "schema:name": "octocat",
                    "schema:url": "https://github.com/octocat",
                    "pulse:githubUsername": "octocat",
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
        _url_info: Any,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="user",
            context={
                "user": {
                    "username": "octocat",
                    "profile": {"login": "octocat"},
                    "owned_repos": [],
                    "orcid_data": None,
                },
            },
        )

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_context_summary_agent=_SummaryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_repository_agent=_LLMNoDataRunner(),
        llm_organization_agent=_LLMNoDataRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _get_json_from_app(
        app,
        "/v2/extract/github.com/octocat",
        params={
            "agent_runtime": "llm",
            "output_format": "json",
            "include_context_summary": "true",
        },
    )

    assert status_code == HTTP_OK
    assert payload["context_summary_markdown"].startswith("# Compiled Context")
    assert V2ExtractResponse.model_validate(payload)


def test_extract_post_can_include_compiled_context_summary_in_response() -> None:
    class _SummaryRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(data={"summary_markdown": "# Compiled Context\n- Key signal"})

    class _LLMPersonRunner:
        async def run(
            self,
            context: dict[str, Any],
            providers: ProviderSet,
        ) -> AgentResult:
            del context, providers
            return AgentResult(
                data={
                    "id": "octocat",
                    "type": "schema:Person",
                    "shacl": "pulse:PersonShape",
                    "identifiers": {
                        "pulse:orcid": None,
                        "pulse:infosciencePersonIdentifier": None,
                        "pulse:githubUsername": "octocat",
                        "uuid": "11111111-1111-4111-8111-111111111111",
                    },
                    "idSource": "pulse:githubUsername",
                    "schema:name": "octocat",
                    "schema:url": "https://github.com/octocat",
                    "pulse:githubUsername": "octocat",
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
        _url_info: Any,
        _providers: ProviderSet,
    ) -> ContextBundle:
        return ContextBundle(
            detected_type="user",
            context={
                "user": {
                    "username": "octocat",
                    "profile": {"login": "octocat"},
                    "owned_repos": [],
                    "orcid_data": None,
                },
            },
        )

    app = _build_test_app()
    app.state.v2_orchestrator = PipelineOrchestrator(
        context_gatherer=_context_gatherer,
        llm_context_summary_agent=_SummaryRunner(),
        llm_person_agent=_LLMPersonRunner(),
        llm_repository_agent=_LLMNoDataRunner(),
        llm_organization_agent=_LLMNoDataRunner(),
        llm_article_agent=_LLMNoDataRunner(),
        llm_membership_agent=_LLMNoDataRunner(),
        llm_contribution_agent=_LLMNoDataRunner(),
        retry_max_retries=0,
        retry_backoff_base=0,
    )

    status_code, payload = _post_json_from_app(
        app,
        "/v2/extract",
        {
            "source_url": "github.com/octocat",
            "agent_runtime": "llm",
            "output_format": "json",
            "include_context_summary": True,
        },
    )

    assert status_code == HTTP_OK
    assert payload["context_summary_markdown"].startswith("# Compiled Context")
    assert V2ExtractResponse.model_validate(payload)


def test_pipeline_cache_round_trip_returns_identical_response(tmp_path: Any) -> None:
    """Two consecutive extract calls with a pipeline cache return the same payload."""
    cache_db = tmp_path / "providers.db"

    async def _run() -> tuple[int, dict, int, dict, int]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            r1 = await client.get("/v2/extract/github.com/octocat/Hello-World")
            r2 = await client.get("/v2/extract/github.com/octocat/Hello-World")
        rows = list(
            app.state.v2_provider_cache._connect().execute(  # noqa: SLF001
                "SELECT 1 FROM responses",
            ),
        )
        return r1.status_code, r1.json(), r2.status_code, r2.json(), len(rows)

    sc1, payload1, sc2, payload2, row_count = asyncio.run(_run())
    assert sc1 == HTTP_OK
    assert sc2 == HTTP_OK
    assert payload1 == payload2
    assert row_count >= 1


def test_pipeline_cache_distinguishes_output_format(tmp_path: Any) -> None:
    """Different output_format values produce distinct cache entries."""
    cache_db = tmp_path / "providers.db"

    async def _run() -> tuple[int, str, int, str]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            r_jsonld = await client.get(
                "/v2/extract/github.com/octocat/Hello-World",
                params={"output_format": "jsonld"},
            )
            r_json = await client.get(
                "/v2/extract/github.com/octocat/Hello-World",
                params={"output_format": "json"},
            )
        return (
            r_jsonld.status_code,
            r_jsonld.json()["output_format"],
            r_json.status_code,
            r_json.json()["output_format"],
        )

    sc1, fmt1, sc2, fmt2 = asyncio.run(_run())
    assert sc1 == HTTP_OK
    assert fmt1 == "jsonld"
    assert sc2 == HTTP_OK
    assert fmt2 == "json"
