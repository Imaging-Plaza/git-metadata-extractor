"""Exercise request scope and pipeline caches through snapshot HTTP adapters."""

# ruff: noqa: PLC0415, SLF001
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest


def test_pipeline_cache_separates_modes_and_snapshot_revisions(tmp_path, monkeypatch):
    from git_metadata_extractor.providers.cache import ProviderCache
    from git_metadata_extractor.providers.runtime import build_provider_resources
    from git_metadata_extractor.providers.snapshot import (
        SnapshotInfoscienceProvider,
        SnapshotORCIDProvider,
        SnapshotRORProvider,
    )
    from git_metadata_extractor.providers.sources_service import SourcesClient
    from git_metadata_extractor.services.extraction import ExtractionOptions, execute_extraction

    monkeypatch.setenv("V2_PIPELINE_CACHE_ENABLED", "true")
    monkeypatch.setenv("V2_SOURCE_MODE", "direct")
    manifest_version = ["v1"]
    paths = []
    direct_resources = build_provider_resources(SimpleNamespace(), use_mock=True, include_rag=False)
    direct = direct_resources.bundle

    def descriptors():
        return {name: {"source": name, "id": f"{name}-fixture", "version": manifest_version[0]}
                for name in ("ror", "orcid", "infoscience")}

    def respond(request):
        import json

        path = request.url.path
        paths.append(path)
        snapshots = descriptors()
        if path.endswith("manifest"):
            methods = {
                "ror": ["get_organization", "search_organizations"],
                "orcid": ["get_person_by_orcid", "search_persons"],
                "infoscience": ["search_person", "search_orgunit", "search_publications"],
            }
            data = {"fingerprint": manifest_version[0], "sources": {
                key: {**value, "available": True, "methods": methods[key]}
                for key, value in snapshots.items()
            }}
            used = []
        else:
            provider, method = path.rsplit("/", 2)[-2:]
            if provider == "extractor":
                data, used = {}, []
            else:
                kwargs = json.loads(request.content)
                data = getattr(getattr(direct, provider), method)(**kwargs)
                used = [snapshots[provider]]
        return httpx.Response(200, json={"data": data, "service_version": "fixture", "snapshots": used})

    async def exercise():
        client = SourcesClient(
            "http://sources", "token", source_mode="snapshot",
            transport=httpx.MockTransport(respond), sync_transport=httpx.MockTransport(respond),
        )
        snapshot = direct.model_copy(update={
            "sources": client, "ror": SnapshotRORProvider(client),
            "orcid": SnapshotORCIDProvider(client), "infoscience": SnapshotInfoscienceProvider(client),
        })
        cache = ProviderCache(tmp_path / "cache.db")
        options = ExtractionOptions(
            source_url="https://github.com/octocat/Hello-World", agent_runtime="rule_based",
            source_mode="direct", read_only=True, include_run_report=True,
        )
        try:
            first = await execute_extraction(options, providers=direct, cache=cache)
            snapshot_options = options.model_copy(update={"source_mode": "snapshot"})
            second = await execute_extraction(snapshot_options, providers=snapshot, cache=cache)
            third = await execute_extraction(snapshot_options, providers=snapshot, cache=cache)
            manifest_version[0] = "v2"
            fourth = await execute_extraction(snapshot_options, providers=snapshot, cache=cache)
            assert [r.cache_hit for r in (first, second, third, fourth)] == [False, False, True, False]
            assert first.response.source_mode == "direct"
            assert second.response.source_mode == third.response.source_mode == "snapshot"
            assert third.response.run_report.source_mode == "snapshot"
            assert fourth.response.run_report.source_mode == "snapshot"
            assert all(path.startswith("/v2/snapshots/") for path in paths)
        finally:
            await client.aclose()
            await direct_resources.aclose()

    asyncio.run(exercise())


def test_snapshot_manifest_mismatch_on_first_response_records_gap():
    from git_metadata_extractor.providers.sources_service import (
        SourcesClient, current_source_gaps, current_source_snapshots, sources_scope,
    )

    def respond(request):
        if request.url.path.endswith("manifest"):
            return httpx.Response(200, json={"data": {
                "fingerprint": "v1", "sources": {
                    "ror": {"source": "ror", "id": "ror-fixture", "version": "v1",
                            "available": True, "methods": ["search_organizations"]},
                },
            }})
        return httpx.Response(200, json={"data": [], "snapshots": [
            {"source": "ror", "id": "ror-fixture", "version": "v2"},
        ]})

    async def exercise():
        client = SourcesClient("http://sources", "token", source_mode="snapshot", transport=httpx.MockTransport(respond))
        try:
            with sources_scope(client):
                assert await client.prepare_snapshots()
                await client.retrieve("ror", "search_organizations", {"query": "EPFL"})
                assert current_source_snapshots() == []
                assert any("identity changed" in gap for gap in current_source_gaps())
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_snapshot_primary_response_requires_snapshot_descriptor():
    from git_metadata_extractor.providers.base import ProviderError
    from git_metadata_extractor.providers.sources_service import (
        SourcesClient, current_source_gaps, sources_scope,
    )

    async def exercise():
        client = SourcesClient("http://sources", "token", source_mode="snapshot",
                               sync_transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"data": []})))
        try:
            with sources_scope(client):
                with pytest.raises(ProviderError):
                    client.snapshot_lookup("ror", "search_organizations", {"query": "EPFL"})
                assert current_source_gaps()
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_core_extraction_rejects_live_primary_adapters_in_snapshot_bundle(monkeypatch):
    from git_metadata_extractor.providers.runtime import build_provider_resources
    from git_metadata_extractor.providers.sources_service import SourcesClient
    from git_metadata_extractor.services import extraction

    async def must_not_execute(*_args, **_kwargs):
        pytest.fail("The unvalidated bundle reached pipeline execution")

    monkeypatch.setattr(extraction, "_execute", must_not_execute)

    async def exercise():
        resources = build_provider_resources(SimpleNamespace(), use_mock=True, include_rag=False)
        client = SourcesClient("http://sources", "token", source_mode="snapshot",
                               transport=httpx.MockTransport(lambda _request: httpx.Response(503)))
        bundle = resources.bundle.model_copy(update={"sources": client})
        try:
            with pytest.raises(ValueError, match="snapshot"):
                await extraction.execute_extraction(
                    extraction.ExtractionOptions(source_url="https://github.com/example/repo", source_mode="snapshot"),
                    providers=bundle,
                )
        finally:
            await client.aclose()
            await resources.aclose()
    asyncio.run(exercise())


def test_concurrent_extractions_isolate_mode_and_thread_recorded_gaps(monkeypatch):
    from git_metadata_extractor.providers.runtime import build_provider_resources
    from git_metadata_extractor.providers.snapshot import (
        SnapshotInfoscienceProvider, SnapshotORCIDProvider, SnapshotRORProvider,
    )
    from git_metadata_extractor.providers.sources_service import (
        SourcesClient, current_source_gaps, current_source_mode, record_source_gap,
    )
    from git_metadata_extractor.services import extraction

    async def exercise():
        ready = {mode: asyncio.Event() for mode in ("direct", "snapshot")}

        async def execute_probe(_options, **_kwargs):
            mode = current_source_mode()
            await asyncio.to_thread(record_source_gap, f"{mode}-only")
            ready[mode].set()
            await ready["snapshot" if mode == "direct" else "direct"].wait()
            assert current_source_mode() == mode
            assert current_source_gaps() == [f"{mode}-only"]
            return SimpleNamespace(
                cache_hit=False,
                response=SimpleNamespace(stats=SimpleNamespace(run_id=mode), warnings=current_source_gaps()),
            )

        monkeypatch.setattr(extraction, "_execute", execute_probe)
        resources = build_provider_resources(SimpleNamespace(), use_mock=True, include_rag=False)
        client = SourcesClient("http://sources", "token", source_mode="snapshot", transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"data": {"fingerprint": "v1", "sources": {}}}),
        ))
        snapshot = resources.bundle.model_copy(update={
            "sources": client, "ror": SnapshotRORProvider(client),
            "orcid": SnapshotORCIDProvider(client), "infoscience": SnapshotInfoscienceProvider(client),
        })
        try:
            await asyncio.gather(*[
                extraction.execute_extraction(
                    extraction.ExtractionOptions(source_url="https://github.com/example/repo", source_mode=mode),
                    providers=bundle,
                ) for mode, bundle in (("direct", resources.bundle), ("snapshot", snapshot))
            ])
            assert current_source_mode() == "direct"
            assert current_source_gaps() == []
        finally:
            await client.aclose()
            await resources.aclose()
    asyncio.run(exercise())


def test_snapshot_agent_verdict_cache_neither_reads_nor_writes():
    from git_metadata_extractor.agents.llm._verdict_cache import (
        get_cached_agent_verdict, store_agent_verdict,
    )
    from git_metadata_extractor.agents.models import AgentResult
    from git_metadata_extractor.providers.sources_service import sources_scope

    cache = Mock()
    cache.get.return_value = {"data": {"id": "historical-source-identity"}}
    client = SimpleNamespace(source_mode="snapshot", cache_identity="snapshot-fixture")
    with sources_scope(client):
        assert get_cached_agent_verdict(
            cache, agent_name="person", identity={"login": "example"},
        ) is None
        store_agent_verdict(
            cache, agent_name="person", identity={"login": "example"},
            result=AgentResult(data={"id": "current-source-identity"}),
        )
    cache.get.assert_not_called()
    cache.set.assert_not_called()
