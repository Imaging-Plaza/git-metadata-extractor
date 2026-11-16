"""Snapshot selection stays request-scoped and never falls through to live HTTP."""
import asyncio
import json
import threading

import httpx
import pytest

from git_metadata_extractor.pipeline.stages.raw_projection import source_snapshot
from git_metadata_extractor.providers.base import ProviderError
from git_metadata_extractor.providers.ror_rag import RorRagProvider
from git_metadata_extractor.providers.snapshot import SnapshotRORProvider
from git_metadata_extractor.providers.sources_service import (
    SnapshotDescriptor,
    SourcesClient,
    current_source_gaps,
    current_source_mode,
    current_source_snapshots,
    current_sources_fingerprint,
    sources_scope,
)


def test_snapshot_manifest_partitions_cache_and_records_only_used_snapshots():
    snapshot = {"source": "ror", "id": "ror-2026", "version": "v1"}
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path.endswith("manifest"):
            return httpx.Response(200, json={"data": {"fingerprint": "manifest-v1", "sources": {"ror": {**snapshot, "available": True, "methods": ["search"]}}},
                                             "service_version": "test", "snapshots": []})
        return httpx.Response(200, json={"data": [], "service_version": "test", "snapshots": [snapshot]})

    async def exercise():
        client = SourcesClient("http://sources", "token", source_mode="snapshot", transport=httpx.MockTransport(respond))
        direct = SourcesClient("http://sources", "token", transport=httpx.MockTransport(respond))
        try:
            with sources_scope(client):
                before = current_sources_fingerprint()
                assert await client.prepare_snapshots()
                assert current_sources_fingerprint() not in {before, direct.cache_identity}
                assert current_source_snapshots() == []
                result = await client.retrieve("ror", "search", {"query": "EPFL"})
                assert result.status == "ok"
                assert [s.model_dump() for s in current_source_snapshots()] == [snapshot]
            assert current_source_snapshots() == []
        finally:
            await client.aclose()
            await direct.aclose()
    asyncio.run(exercise())
    assert paths == ["/v2/snapshots/manifest", "/v2/snapshots/ror/search"]


@pytest.mark.parametrize("method", ["search_organizations", "get_organization", "search"])
def test_wrong_snapshot_result_shape_records_gap(method):
    data = [] if method == "get_organization" else {}
    descriptor = {"source": "ror", "id": "ror-fixture", "version": "v1"}

    def respond(_request):
        return httpx.Response(200, json={"data": data, "snapshots": [descriptor]})

    async def exercise():
        client = SourcesClient("http://sources", "token", source_mode="snapshot",
                               transport=httpx.MockTransport(respond), sync_transport=httpx.MockTransport(respond))
        try:
            with sources_scope(client):
                if method == "search":
                    assert await RorRagProvider(client=client).search("EPFL") == []
                else:
                    with pytest.raises(ProviderError):
                        await asyncio.to_thread(getattr(SnapshotRORProvider(client), method), "EPFL")
                assert current_source_gaps()
        finally:
            await client.aclose()

    asyncio.run(exercise())
def test_snapshot_unsupported_operation_does_not_call_direct_endpoint():
    paths = []
    def respond(request):
        paths.append(request.url.path)
        return httpx.Response(501, json={"detail": "unsupported"})

    async def exercise():
        client = SourcesClient("http://sources", "token", source_mode="snapshot", transport=httpx.MockTransport(respond))
        try:
            with sources_scope(client):
                result = await client.operation("zenodo-community", {"query": "project"})
                assert result.status != "ok"
                assert current_source_gaps()
        finally:
            await client.aclose()
    asyncio.run(exercise())
    assert paths == ["/v2/snapshots/extractor/zenodo-community"]


def test_sync_snapshot_lookup_propagates_thread_observations_and_raw_identity():
    snapshot = {"source": "ror", "id": "ror-2026", "version": "export-42"}
    def respond(request):
        assert request.url.path == "/v2/snapshots/ror/get_organization"
        return httpx.Response(200, json={"data": {"name": "EPFL"}, "service_version": "build-123",
                                       "snapshots": [snapshot]})

    async def exercise():
        client = SourcesClient("http://sources", "token", source_mode="snapshot",
                               sync_transport=httpx.MockTransport(respond))
        try:
            with sources_scope(client):
                result = await asyncio.to_thread(client.snapshot_lookup, "ror", "get_organization", {"ror_id": "x"})
                assert result == {"name": "EPFL"}
                assert len(current_source_snapshots()) == 1
                node = source_snapshot("pulse:ROR")
                assert node["schema:softwareVersion"] == "export-42"
                assert node["schema:name"] == "ror-2026"
                assert source_snapshot("pulse:ORCID") is None
                assert source_snapshot("pulse:GitHub") is None
        finally:
            await client.aclose()
    asyncio.run(exercise())


def test_concurrent_modes_keep_metadata_isolated():
    async def exercise():
        direct = SourcesClient("http://sources", "token")
        snapshot = SourcesClient("http://sources", "token", source_mode="snapshot")
        async def run(client, mode):
            with sources_scope(client):
                await asyncio.sleep(0)
                assert current_source_mode() == mode
                assert current_source_gaps() == []
        try:
            await asyncio.gather(run(direct, "direct"), run(snapshot, "snapshot"))
        finally:
            await direct.aclose()
            await snapshot.aclose()
    asyncio.run(exercise())


@pytest.mark.parametrize("available", [None, False])
def test_snapshot_absent_from_prepared_manifest_disables_cache_and_attribution(available):
    descriptor = {"source": "ror", "id": "new-dataset", "version": "v2"}

    def respond(request):
        if request.url.path.endswith("manifest"):
            sources = {} if available is None else {"ror": {**descriptor, "available": available, "methods": ["search"]}}
            return httpx.Response(200, json={"data": {"fingerprint": "old-registry", "sources": sources}, "snapshots": []})
        return httpx.Response(200, json={"data": [], "snapshots": [descriptor]})

    async def exercise():
        client = SourcesClient("http://sources", "token", source_mode="snapshot", transport=httpx.MockTransport(respond))
        try:
            with sources_scope(client):
                assert await client.prepare_snapshots()
                await client.retrieve("ror", "search", {"query": "EPFL"})
                assert current_source_gaps()
                assert current_source_snapshots() == []
        finally:
            await client.aclose()

    asyncio.run(exercise())


def test_conflicting_worker_response_cannot_restore_snapshot_attribution(monkeypatch):
    comparing = threading.Event()
    release = threading.Event()
    original_equal = SnapshotDescriptor.__eq__
    descriptor = {"source": "ror", "id": "ror-fixture", "version": "v1"}

    def compare(self, other):
        if self.version == "v1" and isinstance(other, SnapshotDescriptor) and other.version == "v1":
            comparing.set()
            assert release.wait(timeout=5)
        return original_equal(self, other)

    monkeypatch.setattr(SnapshotDescriptor, "__eq__", compare)

    def respond(request):
        if request.url.path.endswith("manifest"):
            sources = {"ror": {**descriptor, "available": True, "methods": ["search_organizations"]}}
            return httpx.Response(200, json={"data": {"fingerprint": "v1", "sources": sources}})
        version = json.loads(request.content)["query"]
        return httpx.Response(200, json={"data": [], "snapshots": [{**descriptor, "version": version}]})

    async def exercise():
        client = SourcesClient("http://sources", "token", source_mode="snapshot",
                               transport=httpx.MockTransport(respond), sync_transport=httpx.MockTransport(respond))
        try:
            with sources_scope(client):
                assert await client.prepare_snapshots()
                good = asyncio.create_task(asyncio.to_thread(client.snapshot_lookup, "ror", "search_organizations", {"query": "v1"}))
                try:
                    assert await asyncio.to_thread(comparing.wait, 5)
                    bad = asyncio.create_task(asyncio.to_thread(client.snapshot_lookup, "ror", "search_organizations", {"query": "v2"}))
                    await asyncio.wait({bad}, timeout=0.1)
                finally:
                    release.set()
                await asyncio.gather(good, bad)
                assert current_source_gaps()
                assert current_source_snapshots() == []
        finally:
            release.set()
            await client.aclose()

    asyncio.run(exercise())
