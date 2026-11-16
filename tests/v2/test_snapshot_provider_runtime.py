"""Strict snapshot bundles never borrow direct scholarly clients or fall back."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.providers import runtime
from git_metadata_extractor.providers.base import ProviderError
from git_metadata_extractor.providers.github_provider import RealGitHubProvider
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider


class SnapshotClient:
    def __init__(self, base_url, token, *, timeout, source_mode):
        del base_url, token, timeout
        self.source_mode = source_mode
        self.calls = []
        self.closed = False
        self.response = None

    def snapshot_lookup(self, provider, method, payload):
        self.calls.append((provider, method, payload))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self):
        self.closed = True

    async def aclose(self):
        self.close()


@pytest.fixture
def snapshot_factory(monkeypatch):
    monkeypatch.setenv("SOURCES_API_URL", "http://sources.test")
    monkeypatch.setenv("SOURCES_API_TOKEN", "test-token")
    monkeypatch.setattr(runtime, "SourcesClient", SnapshotClient)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Strict snapshot mode constructed a live scholarly provider")

    for name in ("RealRORProvider", "RealORCIDProvider", "RealInfoscienceProvider", "_optional_orcid_oauth_session", "_resolve_package_registry_provider"):
        monkeypatch.setattr(runtime, name, forbidden)
    return runtime.build_provider_resources


def test_snapshot_factory_ignores_direct_overrides_and_owns_isolated_clients(snapshot_factory):
    direct_ror = MockRORProvider()
    state = SimpleNamespace(v2_ror_provider=direct_ror)
    first = snapshot_factory(state, use_mock=True, source_mode="snapshot")
    second = snapshot_factory(state, use_mock=True, source_mode="snapshot")
    assert isinstance(first.bundle.github, MockGitHubProvider)
    assert first.bundle.ror is not direct_ror
    assert first.bundle.sources is not second.bundle.sources
    assert state.v2_ror_provider is direct_ror
    assert not hasattr(state, "v2_sources_provider")
    assert first.bundle.package_registry is None
    for registration in runtime.RAG_PROVIDERS:
        provider = getattr(first.bundle, registration.name)
        assert provider._client is first.bundle.sources  # noqa: SLF001 - verify composition isolation
    asyncio.run(first.aclose())
    assert first.bundle.sources.closed
    assert not second.bundle.sources.closed
    asyncio.run(second.aclose())


def test_snapshot_factory_requires_service_even_with_mock_github(monkeypatch):
    monkeypatch.delenv("SOURCES_API_URL", raising=False)
    with pytest.raises(ValueError, match="SOURCES_API_URL"):
        runtime.build_provider_resources(SimpleNamespace(), source_mode="snapshot", use_mock=True)


def test_snapshot_mode_preserves_real_github_and_repository_scope(snapshot_factory):
    resources = snapshot_factory(
        SimpleNamespace(), source_mode="snapshot", use_mock=False,
        cache=None, repository_scope=True, include_rag=False,
    )
    assert isinstance(resources.bundle.github, RealGitHubProvider)
    assert not resources.bundle.github.include_user_repositories
    assert not resources.bundle.github.include_organization_repositories
    assert resources.bundle.sources.source_mode == "snapshot"
    assert resources.bundle.package_registry is None
    asyncio.run(resources.aclose())


def test_matching_injected_snapshot_bundle_is_borrowed(snapshot_factory):
    original = snapshot_factory(SimpleNamespace(), source_mode="snapshot", use_mock=True)
    borrowed = snapshot_factory(SimpleNamespace(v2_provider_set=original.bundle), source_mode="snapshot")
    assert borrowed.bundle is original.bundle
    asyncio.run(borrowed.aclose())
    assert not original.bundle.sources.closed
    asyncio.run(original.aclose())


def test_snapshot_factory_rejects_direct_injected_bundle(snapshot_factory):
    state = SimpleNamespace(v2_provider_set=ProviderSet(github=MockGitHubProvider(), ror=MockRORProvider()))
    with pytest.raises(ValueError, match="source_mode"):
        snapshot_factory(state, source_mode="snapshot")


def test_direct_request_rejects_snapshot_primary_override(snapshot_factory):
    snapshot = snapshot_factory(SimpleNamespace(), source_mode="snapshot", use_mock=True, include_rag=False)
    state = SimpleNamespace(v2_ror_provider=snapshot.bundle.ror)
    try:
        with pytest.raises(ValueError, match="source_mode"):
            snapshot_factory(state, source_mode="direct", use_mock=True, include_rag=False)
    finally:
        asyncio.run(snapshot.aclose())


def test_direct_injected_bundle_cannot_hide_snapshot_primary(snapshot_factory):
    snapshot = snapshot_factory(SimpleNamespace(), source_mode="snapshot", use_mock=True, include_rag=False)
    mixed = snapshot.bundle.model_copy(update={"sources": None})
    try:
        with pytest.raises(ValueError, match="source_mode"):
            snapshot_factory(SimpleNamespace(v2_provider_set=mixed), source_mode="direct")
    finally:
        asyncio.run(snapshot.aclose())


def test_snapshot_primary_calls_preserve_payloads_and_failures(snapshot_factory):
    resources = snapshot_factory(SimpleNamespace(), source_mode="snapshot", use_mock=True, include_rag=False)
    bundle = resources.bundle
    client = bundle.sources
    client.response = {"id": "https://ror.org/02s376052", "name": "EPFL"}
    assert bundle.ror.get_organization("02s376052")["name"] == "EPFL"
    client.response = [{"id": "https://ror.org/02s376052", "name": "EPFL"}]
    assert bundle.ror.search_organizations("EPFL")[0]["id"] == "https://ror.org/02s376052"
    client.response = {"orcid_id": "0000-0002-1825-0097", "name": "Example", "employment": [], "education": [], "affiliations": []}
    assert bundle.orcid.get_person_by_orcid("0000-0002-1825-0097")["name"] == "Example"
    client.response = []
    assert bundle.orcid.search_persons("Example", rows=3, start=2) == []
    assert bundle.infoscience.search_person("Example") == []
    assert bundle.infoscience.search_orgunit("EPFL") == []
    assert bundle.infoscience.search_publications("gimie") == []
    assert client.calls == [
        ("ror", "get_organization", {"ror_id": "02s376052"}),
        ("ror", "search_organizations", {"query": "EPFL"}),
        ("orcid", "get_person_by_orcid", {"orcid_id": "0000-0002-1825-0097"}),
        ("orcid", "search_persons", {"query": "Example", "rows": 3, "start": 2}),
        ("infoscience", "search_person", {"query": "Example"}),
        ("infoscience", "search_orgunit", {"query": "EPFL"}),
        ("infoscience", "search_publications", {"query": "gimie"}),
    ]
    client.response = ProviderError("snapshot unavailable")
    with pytest.raises(ProviderError, match="snapshot unavailable"):
        bundle.ror.search_organizations("EPFL")
    asyncio.run(resources.aclose())


@pytest.mark.parametrize("response", [{}, ["bad"], None])
def test_snapshot_search_rejects_malformed_results(snapshot_factory, response):
    resources = snapshot_factory(SimpleNamespace(), source_mode="snapshot", use_mock=True, include_rag=False)
    resources.bundle.sources.response = response
    with pytest.raises(ProviderError, match="response"):
        resources.bundle.ror.search_organizations("EPFL")
    asyncio.run(resources.aclose())
