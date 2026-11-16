"""Optional Infoscience outages must not freeze degraded graphs in cache."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from git_metadata_extractor.agents import ArticleAgentV2, ProviderSet
from git_metadata_extractor.pipeline.orchestrator import PipelineOrchestrator
from git_metadata_extractor.providers.base import ProviderError
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.providers.runtime import build_provider_resources
from git_metadata_extractor.services.extraction import (
    ExtractionOptions,
    execute_extraction,
)


@pytest.mark.parametrize("case", [
    ("search_person", "https://github.com/octocat", "person_agent"),
    ("search_orgunit", "https://github.com/orgs/github", "org_agent"),
    ("search_publications", "https://github.com/octocat/Hello-World", "article_agent"),
])
def test_infoscience_outage_is_retried_before_healthy_graph_is_cached(tmp_path, monkeypatch, case):
    method, url, stage_prefix = case
    monkeypatch.setenv("V2_PIPELINE_CACHE_ENABLED", "true")
    monkeypatch.setenv("V2_QUERY_LOG_DIR", str(tmp_path / "queries"))

    async def run():
        resources = build_provider_resources(SimpleNamespace(), use_mock=True, include_rag=False)
        cache = ProviderCache(tmp_path / "cache.db")
        orchestrator = PipelineOrchestrator(cache=cache)
        execute = orchestrator.execute
        pipeline_results = []

        async def recording_execute(**kwargs):
            result = await execute(**kwargs)
            pipeline_results.append(result)
            return result

        monkeypatch.setattr(orchestrator, "execute", recording_execute)
        provider = resources.bundle.infoscience
        search = getattr(provider, method)
        healthy = False
        calls = []

        def intermittently_available(query):
            calls.append(query)
            if not healthy:
                message = "Infoscience temporarily unavailable"
                raise ProviderError(message)
            return search(query)

        monkeypatch.setattr(provider, method, intermittently_available)
        options = ExtractionOptions(source_url=url, agent_runtime="rule_based", read_only=True)
        try:
            first = await execute_extraction(
                options, providers=resources.bundle, orchestrator=orchestrator, cache=cache,
            )
            assert not first.cache_hit
            assert first.response.output
            assert calls
            calls_before = len(calls)
            healthy = True
            second = await execute_extraction(
                options, providers=resources.bundle, orchestrator=orchestrator, cache=cache,
            )
            assert not second.cache_hit, "An enrichment outage was stored as a reusable graph"
            assert len(calls) > calls_before
            degraded = [
                result for name, result in pipeline_results[0].agent_results.items()
                if name.startswith(stage_prefix)
                and result.stats.get("unavailable_providers") == ["infoscience"]
            ]
            assert degraded, "Availability metadata was lost before the cache decision"
            assert all(not result.is_partial for result in degraded)
            assert all(
                not result.stats.get("unavailable_providers")
                for result in pipeline_results[1].agent_results.values()
            )
            calls_after_recovery = len(calls)
            third = await execute_extraction(
                options, providers=resources.bundle, orchestrator=orchestrator, cache=cache,
            )
            assert third.cache_hit
            assert len(calls) == calls_after_recovery
        finally:
            await resources.aclose()

    asyncio.run(run())


def test_article_partial_search_success_retains_articles_and_unavailability(monkeypatch):
    provider = MockInfoscienceProvider()

    def search(query):
        if query == "octocat/Hello-World":
            message = "Infoscience temporarily unavailable"
            raise ProviderError(message)
        return [{
            "title": "Metadata tooling", "doi": "10.1000/metadata",
            "authors": ["Alice Example"], "publicationDate": "2025-01-01",
        }]

    monkeypatch.setattr(provider, "search_publications", search)
    result = asyncio.run(ArticleAgentV2().run(
        {"detected_type": "repository", "full_name": "octocat/Hello-World",
         "known_persons": [{"id": "https://github.com/alice", "schema:name": "Alice Example"}]},
        ProviderSet(github=MockGitHubProvider(), infoscience=provider),
    ))
    assert result.stats["articles"]
    assert result.stats.get("unavailable_providers") == ["infoscience"]
    assert not result.is_partial


def test_pre_availability_pipeline_entry_is_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setenv("V2_PIPELINE_CACHE_ENABLED", "true")
    monkeypatch.setenv("V2_QUERY_LOG_DIR", str(tmp_path / "queries"))
    make_key = ProviderCache.make_key

    def legacy_key(provider, operation, **kwargs):
        kwargs.pop("provider_availability_version", None)
        return make_key(provider, operation, **kwargs)

    async def run():
        resources = build_provider_resources(SimpleNamespace(), use_mock=True, include_rag=False)
        cache = ProviderCache(tmp_path / "cache.db")
        options = ExtractionOptions(
            source_url="https://github.com/octocat", agent_runtime="rule_based", read_only=True,
        )
        try:
            with monkeypatch.context() as legacy:
                legacy.setattr(ProviderCache, "make_key", staticmethod(legacy_key))
                first = await execute_extraction(options, providers=resources.bundle, cache=cache)
                assert not first.cache_hit
                cached = await execute_extraction(options, providers=resources.bundle, cache=cache)
                assert cached.cache_hit
            refreshed = await execute_extraction(options, providers=resources.bundle, cache=cache)
            assert not refreshed.cache_hit
            cached = await execute_extraction(options, providers=resources.bundle, cache=cache)
            assert cached.cache_hit
        finally:
            await resources.aclose()

    asyncio.run(run())
