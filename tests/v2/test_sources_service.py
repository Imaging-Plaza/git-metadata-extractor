"""Read-only source HTTP boundary, tested without Qdrant or an embedding model."""

# Integration imports stay local so each boundary test names its collaborators.
# ruff: noqa: PLC0415

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from git_metadata_extractor.providers.sources_service import (
    SourcesClient,
    SourceSearchRequest,
)


def test_search_uses_service_contract_and_preserves_source_metadata():
    def handler(request):
        assert request.url.path == "/v2/indices/ror/search"
        assert request.headers["authorization"] == "Bearer test-credential"
        assert json.loads(request.content) == {"query": "EPFL", "top_k": 10}
        return httpx.Response(
            200,
            json={
                "index_name": "ror",
                "query": "EPFL",
                "hits": [
                    {"id": "https://ror.org/example", "payload": {"name": "EPFL"}},
                ],
                "extra": {
                    "snapshot_id": "build-42",
                    "embedding_model": "model-from-service",
                },
            },
        )

    async def run():
        client = SourcesClient(
            "http://sources:8001",
            "test-credential",
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await client.search("ror", SourceSearchRequest(query="EPFL"))
            assert result.status == "ok"
            assert result.hits[0].id == "https://ror.org/example"
            assert result.metadata["snapshot_id"] == "build-42"
        finally:
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("code", "body", "expected"),
    [
        (200, {"index_name": "ror", "query": "EPFL", "hits": []}, "no_matches"),
        (503, {"detail": "secret upstream body"}, "unavailable"),
        (401, {"detail": "secret upstream body"}, "unauthorized"),
        (404, {}, "unsupported"),
        (200, {"hits": "wrong shape"}, "invalid_response"),
        (200, {"index_name": "orcid", "query": "EPFL", "hits": []}, "invalid_response"),
        (
            200,
            {
                "index_name": "ror",
                "query": "EPFL",
                "hits": [],
                "extra": {"error": "secret upstream body"},
            },
            "error",
        ),
    ],
)
def test_empty_outage_auth_and_invalid_responses_are_distinct(code, body, expected):
    async def run():
        client = SourcesClient(
            "http://sources:8001",
            "test-credential",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(code, json=body),
            ),
        )
        try:
            result = await client.search("ror", SourceSearchRequest(query="EPFL"))
            assert result.status == expected
            assert "secret upstream body" not in result.model_dump_json()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_invalid_query_and_index_are_rejected_before_network():
    with pytest.raises(ValidationError):
        SourceSearchRequest(query="EPFL", top_k=201)
    with pytest.raises(ValidationError):
        SourceSearchRequest(query="   ")

    async def run():
        client = SourcesClient("http://sources:8001", "test-credential")
        try:
            with pytest.raises(ValidationError):
                await client.search("../reset", SourceSearchRequest(query="EPFL"))
        finally:
            await client.aclose()

    asyncio.run(run())


def test_remote_mode_builds_no_local_index_clients(monkeypatch):
    from git_metadata_extractor.providers import runtime

    monkeypatch.setenv("SOURCES_API_URL", "http://sources:8001")
    monkeypatch.setenv("SOURCES_API_TOKEN", "test-credential")

    def forbidden(*_args):
        pytest.fail("Remote mode must not construct a local index provider")

    monkeypatch.setattr(runtime, "_resolve_registered_provider", forbidden)
    state = SimpleNamespace()

    async def run():
        resources = runtime.build_provider_resources(state, use_mock=True)
        try:
            assert isinstance(resources.bundle.sources, SourcesClient)
            assert resources.bundle.ror_rag is None
        finally:
            await resources.aclose()
            await runtime.close_shared_providers(state)

    asyncio.run(run())


def test_remote_mode_disables_gunicorn_store_bootstrap(monkeypatch):
    from tools.config.gunicorn_conf import _bootstrap_on_start_enabled

    monkeypatch.setenv("SOURCES_API_URL", "http://sources:8080")
    monkeypatch.setenv("INDEX_BOOTSTRAP_ON_START", "true")
    assert not _bootstrap_on_start_enabled()


def test_remote_mode_requires_explicit_credentials(monkeypatch):
    from git_metadata_extractor.providers.runtime import build_provider_resources

    monkeypatch.setenv("SOURCES_API_URL", "http://sources:8080")
    monkeypatch.delenv("SOURCES_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="SOURCES_API_TOKEN is required"):
        build_provider_resources(SimpleNamespace(), use_mock=True)


def test_manifest_and_catalog_tools_share_the_typed_client():
    from git_metadata_extractor.agents.llm.tool_defaults import default_tool_catalog
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.agents.tool_catalog import (
        ToolContext,
        ToolRef,
        ToolSelection,
    )

    async def run():
        client = SourcesClient(
            "http://sources:8001",
            "test-credential",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json=[
                        {
                            "name": "ror",
                            "duckdb": "ror.duckdb",
                            "entity_types": ["organizations"],
                            "backend": "vector",
                            "surface_as_source": True,
                            "id_shape": "url",
                        },
                    ],
                ),
            ),
        )
        try:
            context = ToolContext(providers=ProviderSet(github=None, sources=client))
            tools = default_tool_catalog().build(
                "repo_agent",
                context,
                ToolSelection(
                    include_defaults=False,
                    tools=[ToolRef(component="sources.remote")],
                ),
            )
            assert [tool.name for tool in tools] == ["list_sources", "search_sources"]
            manifest = await tools[0].function()
            assert manifest["sources"][0]["name"] == "ror"
            assert manifest["status"] == "ok"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_source_backend_scope_isolates_verdict_cache_and_restores_context():
    from git_metadata_extractor.agents.llm._verdict_cache import _verdict_key
    from git_metadata_extractor.providers.sources_service import (
        current_sources_fingerprint,
        sources_scope,
    )

    async def run():
        first = SourcesClient("http://first:8001", "test-credential")
        second = SourcesClient("http://second:8001", "test-credential")
        try:
            legacy = _verdict_key("person", {"id": "same"})
            with sources_scope(first):
                one = _verdict_key("person", {"id": "same"})
                with sources_scope(second):
                    two = _verdict_key("person", {"id": "same"})
                assert current_sources_fingerprint() == first.cache_identity
            assert one != two
            assert legacy not in {one, two}
            assert current_sources_fingerprint() is None
        finally:
            await first.aclose()
            await second.aclose()

    asyncio.run(run())


def test_transport_failure_is_reported_and_cancellation_propagates():
    async def run():
        for failure in (
            httpx.ReadTimeout("upstream credentials must not leak"),
            asyncio.CancelledError(),
        ):

            async def handler(_request, failure=failure):
                raise failure

            client = SourcesClient(
                "http://sources:8001",
                "test-credential",
                transport=httpx.MockTransport(handler),
            )
            try:
                if isinstance(failure, asyncio.CancelledError):
                    with pytest.raises(asyncio.CancelledError):
                        await client.manifest()
                else:
                    result = await client.manifest()
                    assert result.status == "unavailable"
                    assert "credentials" not in result.model_dump_json()
            finally:
                await client.aclose()

    asyncio.run(run())


def test_client_matches_installed_sources_router_and_provenance(monkeypatch):
    from fastapi import FastAPI
    from open_pulse_sources.service import api
    from open_pulse_sources.service.api_models import IndexSearchResponse

    from git_metadata_extractor.observation.evidence import activity, observe_provider
    from git_metadata_extractor.observation.query_log import QueryLog, query_log_var

    async def search(payload, _state):
        return IndexSearchResponse(
            index_name="ror",
            query=payload.query,
            hits=[{"id": "https://ror.org/example", "payload": {"name": "EPFL"}}],
        )

    monkeypatch.setattr(api, "run_ror_search", search)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.verify_token] = lambda: "test-credential"

    async def run():
        client = SourcesClient(
            "http://sources", "test-credential", transport=httpx.ASGITransport(app),
        )
        log = QueryLog(
            run_id="source-contract", extract_full_path="https://github.com/acme/tool",
        )
        token = query_log_var.set(log)
        try:
            provider = observe_provider(client, "sources")
            with activity("organization_agent", kind="llm"):
                result = await provider.search("ror", SourceSearchRequest(query="EPFL"))
                assert result.status == "ok"
                assert any(
                    "https://ror.org/example" in item["sources"]
                    for item in log.evidence.available_sources()
                )
        finally:
            query_log_var.reset(token)
            await client.aclose()

    asyncio.run(run())
