from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.agents.models import AgentResult
from git_metadata_extractor.api import v2_router
from git_metadata_extractor.api_models.contracts import V2ExtractJob, V2ExtractResponse
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.pipeline import PipelineOrchestrator
from git_metadata_extractor.pipeline.stages.models import ContextBundle
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.providers.mock_orcid import MockORCIDProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider

HTTP_OK = 200
HTTP_ACCEPTED = 202
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE_ENTITY = 422
HTTP_SERVICE_UNAVAILABLE = 503

# Matches the value seeded by the `_isolate_v2_runtime_env` autouse
# fixture in `tests/v2/conftest.py`. Every protected request needs a
# matching bearer header — see `git_metadata_extractor/auth.py::verify_token`.
TEST_API_TOKEN = "test-api-token"  # noqa: S105 — test fixture
_AUTH_HEADERS = {"Authorization": f"Bearer {TEST_API_TOKEN}"}


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
            headers=_AUTH_HEADERS,
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
            headers=_AUTH_HEADERS,
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
            headers=_AUTH_HEADERS,
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
            headers=_AUTH_HEADERS,
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


async def _wait_for_job_completion(
    client: AsyncClient,
    job_id: str,
    *,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    """Poll GET /v2/jobs/{job_id} until status is completed or failed."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = await client.get(f"/v2/jobs/{job_id}")
        body = response.json()
        if response.status_code == HTTP_OK and body.get("status") in {
            "completed",
            "failed",
        }:
            return body
        await asyncio.sleep(0.1)
    message = f"job {job_id} did not finish within {timeout_seconds}s"
    raise AssertionError(message)


def test_extract_post_repository_url_runs_async_job(tmp_path: Any) -> None:
    cache_db = tmp_path / "providers.db"

    async def _run() -> tuple[int, dict[str, Any], dict[str, Any]]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH_HEADERS,
        ) as client:
            submit = await client.post(
                "/v2/extract",
                json={
                    "source_url": "github.com/octocat/Hello-World",
                    "agent_runtime": "rule_based",
                },
            )
            submit_payload = submit.json()
            job = await _wait_for_job_completion(client, submit_payload["job_id"])
        return submit.status_code, submit_payload, job

    submit_status, submit_payload, job = asyncio.run(_run())

    assert submit_status == HTTP_ACCEPTED
    assert submit_payload["status"] == "pending"
    assert submit_payload["status_url"] == f"/v2/jobs/{submit_payload['job_id']}"

    assert job["status"] == "completed"
    assert job["result"]["detected_type"] == "repository"
    V2ExtractJob.model_validate(job)
    V2ExtractResponse.model_validate(job["result"])


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
        params={"agent_runtime": "not-a-runtime"},
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


def test_extract_post_can_include_compiled_context_summary_in_response(
    tmp_path: Any,
) -> None:
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

    cache_db = tmp_path / "providers.db"

    async def _run() -> dict[str, Any]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
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
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH_HEADERS,
        ) as client:
            submit = await client.post(
                "/v2/extract",
                json={
                    "source_url": "github.com/octocat",
                    "agent_runtime": "llm",
                    "output_format": "json",
                    "include_context_summary": True,
                },
            )
            assert submit.status_code == HTTP_ACCEPTED
            return await _wait_for_job_completion(client, submit.json()["job_id"])

    job = asyncio.run(_run())
    assert job["status"] == "completed"
    assert job["result"]["context_summary_markdown"].startswith("# Compiled Context")
    V2ExtractResponse.model_validate(job["result"])


def test_extract_post_returns_422_for_unsupported_url(tmp_path: Any) -> None:
    cache_db = tmp_path / "providers.db"

    async def _run() -> tuple[int, dict[str, Any]]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH_HEADERS,
        ) as client:
            response = await client.post(
                "/v2/extract",
                json={"source_url": "github.com/owner/repo/issues/1"},
            )
        return response.status_code, response.json()

    status_code, payload = asyncio.run(_run())
    assert status_code == HTTP_UNPROCESSABLE_ENTITY
    assert payload["error_type"] == "unsupported_url"
    assert payload["detected_path_kind"] == "issues"


def test_get_job_returns_404_for_unknown_id(tmp_path: Any) -> None:
    cache_db = tmp_path / "providers.db"

    async def _run() -> tuple[int, dict[str, Any]]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH_HEADERS,
        ) as client:
            response = await client.get("/v2/jobs/does-not-exist")
        return response.status_code, response.json()

    status_code, payload = asyncio.run(_run())
    assert status_code == HTTP_NOT_FOUND
    assert payload["error_type"] == "not_found"


def test_crawl_status_returns_compact_status_for_completed_job(tmp_path: Any) -> None:
    """GET /v2/crawl/{job_id} mirrors the job's status but omits the result
    graph and links to the full record via `result_url`."""
    cache_db = tmp_path / "providers.db"

    async def _run() -> tuple[int, dict[str, Any], dict[str, Any]]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH_HEADERS,
        ) as client:
            submit = await client.post(
                "/v2/extract",
                json={
                    "source_url": "github.com/octocat/Hello-World",
                    "agent_runtime": "rule_based",
                },
            )
            job_id = submit.json()["job_id"]
            await _wait_for_job_completion(client, job_id)
            crawl = await client.get(f"/v2/crawl/{job_id}")
        return crawl.status_code, crawl.json(), {"job_id": job_id}

    status_code, status_payload, meta = asyncio.run(_run())
    assert status_code == HTTP_OK
    assert status_payload["job_id"] == meta["job_id"]
    assert status_payload["status"] == "completed"
    assert status_payload["result_url"] == f"/v2/jobs/{meta['job_id']}"
    assert status_payload["source_url"].endswith("github.com/octocat/Hello-World")
    # The compact view must NOT carry the (potentially large) result graph.
    assert "result" not in status_payload


def test_crawl_status_returns_404_for_unknown_id(tmp_path: Any) -> None:
    """Parity with GET /v2/jobs/{job_id}: unknown id -> typed 404."""
    cache_db = tmp_path / "providers.db"

    async def _run() -> tuple[int, dict[str, Any]]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH_HEADERS,
        ) as client:
            response = await client.get("/v2/crawl/does-not-exist")
        return response.status_code, response.json()

    status_code, payload = asyncio.run(_run())
    assert status_code == HTTP_NOT_FOUND
    assert payload["error_type"] == "not_found"


def test_extract_post_persists_job_in_provider_cache(tmp_path: Any) -> None:
    cache_db = tmp_path / "providers.db"

    async def _run() -> tuple[str, dict[str, Any]]:
        app = _build_test_app()
        app.state.v2_provider_cache = ProviderCache(cache_db)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_AUTH_HEADERS,
        ) as client:
            submit = await client.post(
                "/v2/extract",
                json={
                    "source_url": "github.com/octocat/Hello-World",
                    "agent_runtime": "rule_based",
                },
            )
            job_id = submit.json()["job_id"]
            await _wait_for_job_completion(client, job_id)
        cached = app.state.v2_provider_cache.get(
            ProviderCache.make_key("v2-extract-job", "record", job_id=job_id),
        )
        return job_id, cached

    job_id, cached = asyncio.run(_run())
    assert isinstance(cached, dict)
    assert cached["job_id"] == job_id
    assert cached["status"] == "completed"


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
            headers=_AUTH_HEADERS,
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
            headers=_AUTH_HEADERS,
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
