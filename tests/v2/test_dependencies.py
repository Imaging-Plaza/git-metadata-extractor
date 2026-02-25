from __future__ import annotations

import asyncio

from fastapi import FastAPI
from starlette.requests import Request

from src.v2.dependencies import get_provider_set
from src.v2.providers.github_provider import RealGitHubProvider
from src.v2.providers.mock_github import MockGitHubProvider


def _build_request(*, query_string: str = "") -> Request:
    app = FastAPI()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v2/extract/github.com/octocat/Hello-World",
        "raw_path": b"/v2/extract/github.com/octocat/Hello-World",
        "query_string": query_string.encode("utf-8"),
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": app,
    }
    return Request(scope)


def test_get_provider_set_uses_mock_provider_by_default(monkeypatch) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "true")
    monkeypatch.delenv("V2_DISABLE_CACHE", raising=False)

    provider_set = asyncio.run(get_provider_set(_build_request()))

    assert isinstance(provider_set.github, MockGitHubProvider)


def test_get_provider_set_respects_force_refresh_query_for_real_provider(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "false")
    monkeypatch.delenv("V2_DISABLE_CACHE", raising=False)

    provider_set = asyncio.run(
        get_provider_set(_build_request(query_string="force_refresh=true")),
    )

    assert isinstance(provider_set.github, RealGitHubProvider)
    assert provider_set.github.force_refresh is True


def test_get_provider_set_respects_v2_disable_cache_env_flag(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "false")
    monkeypatch.setenv("V2_DISABLE_CACHE", "1")

    provider_set = asyncio.run(get_provider_set(_build_request()))

    assert isinstance(provider_set.github, RealGitHubProvider)
    assert provider_set.github.force_refresh is True


def test_get_provider_set_keeps_cache_enabled_when_no_flags(monkeypatch) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "false")
    monkeypatch.delenv("V2_DISABLE_CACHE", raising=False)

    provider_set = asyncio.run(get_provider_set(_build_request()))

    assert isinstance(provider_set.github, RealGitHubProvider)
    assert provider_set.github.force_refresh is False
