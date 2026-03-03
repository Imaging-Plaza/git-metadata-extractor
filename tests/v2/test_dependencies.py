from __future__ import annotations

import asyncio

from fastapi import FastAPI
from starlette.requests import Request

from src.v2.dependencies import get_provider_set
from src.v2.providers.github_provider import RealGitHubProvider
from src.v2.providers.mock_github import MockGitHubProvider


def _build_request(
    *,
    full_path: str = "github.com/octocat/Hello-World",
    query_string: str = "",
) -> Request:
    extract_path = f"/v2/extract/{full_path}"
    app = FastAPI()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": extract_path,
        "raw_path": extract_path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": app,
        "path_params": {"full_path": full_path},
    }
    return Request(scope)


def test_get_provider_set_uses_mock_provider_by_default(monkeypatch) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "true")


    provider_set = asyncio.run(get_provider_set(_build_request()))

    assert isinstance(provider_set.github, MockGitHubProvider)


def test_get_provider_set_creates_real_provider_without_cache(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "false")

    provider_set = asyncio.run(get_provider_set(_build_request()))

    assert isinstance(provider_set.github, RealGitHubProvider)


def test_get_provider_set_disables_github_repo_expansion_for_repository_extract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "false")


    provider_set = asyncio.run(
        get_provider_set(_build_request(full_path="github.com/octocat/Hello-World")),
    )

    assert isinstance(provider_set.github, RealGitHubProvider)
    assert provider_set.github.include_user_repositories is False
    assert provider_set.github.include_organization_repositories is False


def test_get_provider_set_keeps_github_repo_expansion_for_user_extract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "false")


    provider_set = asyncio.run(
        get_provider_set(_build_request(full_path="github.com/octocat")),
    )

    assert isinstance(provider_set.github, RealGitHubProvider)
    assert provider_set.github.include_user_repositories is True
    assert provider_set.github.include_organization_repositories is True
