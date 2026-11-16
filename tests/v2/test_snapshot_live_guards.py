"""Snapshot mode cannot reuse or fetch live scholarly enrichment."""

# ruff: noqa: PLC0415, SLF001

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from git_metadata_extractor.providers import sources_service


@pytest.fixture
def snapshot(monkeypatch):
    gaps = []
    monkeypatch.setattr(sources_service, "current_source_mode", lambda: "snapshot", raising=False)
    monkeypatch.setattr(sources_service, "record_source_gap", gaps.append, raising=False)
    return gaps


def test_snapshot_search_blocks_even_cached_live_results(snapshot):
    from git_metadata_extractor.agents.llm.agent_tools.duckduckgo_search import (
        make_duckduckgo_search_tool,
    )

    cache = Mock()
    cache.get.return_value = {"results": [{"title": "live"}]}
    request = Mock()
    result = make_duckduckgo_search_tool(cache=cache, http_get=request).function("EPFL")
    assert result.get("error") == "snapshot_operation_unsupported"
    assert result["results"] == []
    cache.get.assert_not_called()
    request.assert_not_called()
    assert snapshot


def test_snapshot_selenium_blocks_before_cache_and_dns(snapshot, monkeypatch):
    from git_metadata_extractor.agents.llm.agent_tools import selenium_fetch

    cache = Mock()
    cache.get.return_value = {"fetched": True, "text_excerpt": "live"}
    dns = Mock()
    monkeypatch.setattr(selenium_fetch, "_is_safe_public_url", dns)
    result = selenium_fetch.make_fetch_link_content_tool(cache).function("https://orcid.org/123")
    assert result["fetched"] is False
    assert result["error"] == "snapshot_operation_unsupported"
    cache.get.assert_not_called()
    dns.assert_not_called()
    assert snapshot


def test_snapshot_openalex_does_not_fetch(snapshot, monkeypatch):
    from git_metadata_extractor.pipeline.stages import refine_with_llm

    request = Mock()
    monkeypatch.setattr(refine_with_llm.requests, "get", request)
    assert refine_with_llm._openalex_lookup_doi("10.123/example") is None
    request.assert_not_called()
    assert snapshot


def test_snapshot_wikipedia_does_not_fetch(snapshot):
    from git_metadata_extractor.pipeline.stages.concept_tagging import _wikipedia_lookup

    session = Mock()
    assert _wikipedia_lookup("science", session=session) is None
    session.get.assert_not_called()
    assert snapshot


def test_snapshot_github_drops_cached_orcid_activities_without_mutation(snapshot):
    from git_metadata_extractor.providers.github_provider import RealGitHubProvider

    live = {"login": "example", "orcid": "0000-0002-1825-0097", "orcid_activities": {"employment": ["live"]}}
    cache = Mock()
    cache.get_or_set.return_value = live
    result = RealGitHubProvider(cache=cache).get_user("example")
    assert result["login"] == "example"
    assert result["orcid"] == live["orcid"]
    assert result.get("orcid_activities") is None
    assert live["orcid_activities"] == {"employment": ["live"]}
    assert snapshot


def test_snapshot_github_parser_does_not_scrape_orcid(snapshot, monkeypatch):
    from git_metadata_extractor.providers.github_accounts import users_parser

    browser = Mock()
    monkeypatch.setattr(users_parser.webdriver, "Remote", browser)
    assert users_parser.GitHubUsersParser()._scrape_orcid_activities("0000-0002-1825-0097") is None
    browser.assert_not_called()
    assert snapshot


def test_snapshot_link_veracity_preserves_links(snapshot, monkeypatch):
    from git_metadata_extractor.pipeline.stages import link_veracity

    agent = Mock()
    monkeypatch.setattr(link_veracity, "LLMLinkVeracityAgentV2", agent)
    result = asyncio.run(link_veracity.run_link_veracity_stage(
        entities=[{"id": "https://orcid.org/0000-0002-1825-0097", "type": "schema:Person"}],
        source_url="https://github.com/example/repo", providers=Mock(),
    ))
    assert result.checked_count == 0
    assert result.invalid_links == []
    assert any("snapshot" in warning for warning in result.warnings)
    agent.assert_not_called()
    assert snapshot


def test_snapshot_link_veracity_stage_reports_skipped(snapshot, monkeypatch):
    from git_metadata_extractor.agents.runtime import AgentRuntime
    from git_metadata_extractor.pipeline import run

    monkeypatch.setattr(run, "_gate", lambda _name: lambda _state: True)
    state = SimpleNamespace(runtime=AgentRuntime.LLM, warn=Mock())
    assert run._link_veracity_applies(state) is False
    state.warn.assert_called_once()
    assert snapshot


def test_direct_search_retains_cached_results(monkeypatch):
    from git_metadata_extractor.agents.llm.agent_tools.duckduckgo_search import (
        make_duckduckgo_search_tool,
    )

    monkeypatch.setattr(sources_service, "current_source_mode", lambda: "direct", raising=False)
    cache = Mock()
    cached = {"results": [{"title": "live"}]}
    cache.get.return_value = cached
    assert make_duckduckgo_search_tool(cache=cache).function("EPFL") == cached


def test_direct_github_keeps_orcid_enrichment(monkeypatch):
    from git_metadata_extractor.providers.github_provider import RealGitHubProvider

    monkeypatch.setattr(sources_service, "current_source_mode", lambda: "direct", raising=False)
    payload = {"login": "example", "orcid_activities": {"employment": ["live"]}}
    provider = RealGitHubProvider(user_lookup=lambda _username: payload)
    assert provider.get_user("example") == payload
