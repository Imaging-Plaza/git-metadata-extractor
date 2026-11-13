"""Degraded contributor enumeration must be retried on the next extraction."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.contributors import (
    ContributorCoverage,
    ContributorLookup,
)
from git_metadata_extractor.providers.runtime import build_provider_resources
from git_metadata_extractor.services.extraction import (
    ExtractionOptions,
    execute_extraction,
)


@pytest.mark.parametrize("status", ["partial", "unavailable"])
@pytest.mark.parametrize("kind", ["repository", "user", "organization"])
def test_degraded_contributors_do_not_poison_pipeline_cache(
    tmp_path, monkeypatch, status, kind,
):
    monkeypatch.setenv("V2_PIPELINE_CACHE_ENABLED", "true")
    monkeypatch.setenv("V2_EXPAND_OWNED_REPOS", "true")
    monkeypatch.setenv("V2_QUERY_LOG_DIR", str(tmp_path / "queries"))
    urls = {
        "repository": "https://github.com/octocat/Hello-World",
        "user": "https://github.com/octocat",
        "organization": "https://github.com/orgs/github",
    }

    async def run():
        resources = build_provider_resources(
            SimpleNamespace(), use_mock=True, include_rag=False,
        )
        cache = ProviderCache(tmp_path / "cache.db")
        github = resources.bundle.github
        if kind != "repository":
            method_name = "get_user" if kind == "user" else "get_organization"
            profile_fetcher = getattr(github, method_name)

            def profile_with_repository(name):
                return {
                    **profile_fetcher(name),
                    "repositories": ["octocat/Hello-World"],
                }

            monkeypatch.setattr(github, method_name, profile_with_repository)
        healthy = False
        lookups = []

        def contributors_with_coverage(full_name):
            lookups.append(full_name)
            contributors = (
                github.get_contributors(full_name)
                if healthy or status == "partial"
                else []
            )
            return ContributorLookup(
                contributors=contributors,
                coverage=ContributorCoverage(
                    status="complete" if healthy else status,
                    source="github_rest",
                    reason=None if healthy else "github_http_422",
                    fallback_reason="gimie_no_contributor_identities",
                    returned_count=len(contributors),
                ),
            )

        monkeypatch.setattr(
            github, "get_contributors_with_coverage", contributors_with_coverage,
        )
        options = ExtractionOptions(
            source_url=urls[kind], agent_runtime="rule_based", read_only=True,
        )
        try:
            first = await execute_extraction(
                options, providers=resources.bundle, cache=cache,
            )
            assert not first.cache_hit
            assert any(
                f"Contributor coverage {status}" in warning
                for warning in first.response.warnings
            )
            attempted_before_recovery = len(lookups)
            healthy = True
            second = await execute_extraction(
                options, providers=resources.bundle, cache=cache,
            )
            assert not second.cache_hit, (
                "Degraded response prevented a fresh contributor lookup"
            )
            assert len(lookups) > attempted_before_recovery
            assert not any(
                "Contributor coverage" in warning
                for warning in second.response.warnings
            )
            third = await execute_extraction(
                options, providers=resources.bundle, cache=cache,
            )
            assert third.cache_hit, (
                "Healthy coverage should retain ordinary pipeline caching"
            )
        finally:
            await resources.aclose()

    asyncio.run(run())
