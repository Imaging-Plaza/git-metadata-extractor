# ruff: noqa: PLC0415 - integration tests name collaborators at their use sites
import asyncio
import json
from types import SimpleNamespace

import httpx

from git_metadata_extractor.providers.sources_service import (
    SourcesClient,
    sources_scope,
)


def test_ror_adapter_preserves_search_contract_and_response():
    from git_metadata_extractor.providers.ror_rag import RorRagProvider

    hit = {
        "ror_id": "https://ror.org/example",
        "score": 0.8,
        "scope_mode": "europe",
        "name": "Example",
    }

    def handle(request):
        assert request.url.path == "/v2/retrieval/ror/search"
        assert json.loads(request.content) == {
            "query": "Example",
            "scope_mode": "europe",
            "top_k": 4,
            "filters": {"country_code": "CH"},
            "rerank": False,
        }
        return httpx.Response(200, json={"data": [hit], "service_version": "0.2.0"})

    async def run():
        client = SourcesClient(
            "http://sources", "test", transport=httpx.MockTransport(handle),
        )
        try:
            provider = RorRagProvider(client=client)
            assert await provider.search(
                "Example", scope_mode="europe", top_k=4, filters={"country_code": "CH"},
            ) == [hit]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_operation_version_is_scoped_and_propagates_from_child_tasks():
    from git_metadata_extractor.providers.sources_service import current_sources_version

    async def run(version):
        client = SourcesClient(
            "http://sources",
            "test",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json={"data": {"rows": []}, "service_version": version},
                ),
            ),
        )
        try:
            with sources_scope(client):
                assert current_sources_version() is None
                await asyncio.gather(
                    client.operation("category-ancestors", {"category_ids": [1]}),
                )
                assert current_sources_version() == version
            assert current_sources_version() is None
        finally:
            await client.aclose()

    async def both():
        await asyncio.gather(run("0.2.0"), run("0.3.0"))

    asyncio.run(both())


def test_unconfigured_runtime_has_no_index_backends(monkeypatch):
    from git_metadata_extractor.providers.runtime import _build_index_providers

    monkeypatch.delenv("SOURCES_API_URL", raising=False)
    assert _build_index_providers(SimpleNamespace()) == {}


def test_conflicting_response_versions_omit_snapshot_identity():
    from git_metadata_extractor.providers.sources_service import current_sources_version

    async def run():
        versions = iter(["0.2.0", "0.3.0", "0.2.0"])
        client = SourcesClient(
            "http://sources",
            "test",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"data": {}},
                    headers={"X-Open-Pulse-Sources-Version": next(versions)},
                ),
            ),
        )
        try:
            with sources_scope(client):
                await client.operation("category-ancestors", {})
                assert current_sources_version() == "0.2.0"
                await client.operation("category-ancestors", {})
                assert current_sources_version() is None
                await client.operation("category-ancestors", {})
                assert current_sources_version() is None
        finally:
            await client.aclose()

    asyncio.run(run())


def test_invalid_operation_and_missing_metadata_are_not_trusted():
    import pytest
    from pydantic import ValidationError

    from git_metadata_extractor.providers.sources_service import current_sources_version

    async def run():
        client = SourcesClient(
            "http://sources",
            "test",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json={"data": {}, "service_version": "unknown"},
                ),
            ),
        )
        try:
            with sources_scope(client):
                with pytest.raises(ValidationError):
                    await client.operation("../reset", {})
                await client.operation("category-ancestors", {})
                assert current_sources_version() is None
        finally:
            await client.aclose()

    asyncio.run(run())
