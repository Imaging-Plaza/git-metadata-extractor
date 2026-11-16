"""Optional cross-repository contracts. Run with both checkout roots on PYTHONPATH."""

# ruff: noqa: PLC0415 - optional sources imports are intentionally scoped
import asyncio

import httpx
import pytest

from git_metadata_extractor.providers.sources_service import (
    SourcesClient,
    SourceSearchRequest,
)

pytest.importorskip(
    "open_pulse_sources.service.retrieval",
    reason="Cross-repository contract requires the sources checkout",
)


def test_retrieval_client_calls_real_server_and_preserves_hits(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from open_pulse_sources.retrieval.github_rag import (
        GitHubRagProvider as ServerProvider,
    )
    from open_pulse_sources.service import retrieval

    from git_metadata_extractor.providers.github_rag import GitHubRagProvider
    from git_metadata_extractor.providers.sources_service import (
        current_sources_version,
        sources_scope,
    )

    class Store:
        client = SimpleNamespace(collection_exists=lambda _: True)

        def search(self, collection, *, query_vector, top_k, filter_payload):
            assert collection == "github_repos"
            assert query_vector == [0.1]
            assert top_k == 1
            assert filter_payload == {"owner": "example"}
            return [
                {
                    "id": "one",
                    "score": 0.9,
                    "payload": {
                        "repo_id": "example/project",
                        "owner": "example",
                        "bulky": "omit",
                    },
                },
            ]

    class Embedder:
        async def embed_all(self, texts):
            assert texts == ["example project"]
            return [[0.1]]

    monkeypatch.setenv("API_TOKEN", "test")
    monkeypatch.setattr(retrieval, "service_version", lambda: "contract-build")
    app = FastAPI()
    app.include_router(retrieval.router)
    app.state.retrieval_providers = {
        "github": ServerProvider(store=Store(), embedder=Embedder()),
    }

    async def run():
        client = SourcesClient(
            "http://sources", "test", transport=httpx.ASGITransport(app),
        )
        try:
            with sources_scope(client):
                result = await GitHubRagProvider(client=client).search(
                    "example project", top_k=1, filters={"owner": {"$eq": "example"}},
                )
                assert result == [
                    {
                        "id": "one",
                        "score": 0.9,
                        "collection": "github_repos",
                        "repo_id": "example/project",
                        "owner": "example",
                    },
                ]
                assert current_sources_version() == "contract-build"
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
            "http://sources",
            "test-credential",
            transport=httpx.ASGITransport(app),
        )
        log = QueryLog(
            run_id="source-contract",
            extract_full_path="https://github.com/acme/tool",
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
