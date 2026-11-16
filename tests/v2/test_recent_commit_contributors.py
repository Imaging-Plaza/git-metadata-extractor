"""Oversized contributor histories may yield only a bounded recent-author sample."""

# ruff: noqa: PLR2004, SLF001
from __future__ import annotations

import json

import pytest
import requests

from git_metadata_extractor.pipeline.stages.context_gather import (
    _record_contributor_coverage,
)
from git_metadata_extractor.providers.github_provider import RealGitHubProvider
from git_metadata_extractor.providers.rate_limiter import RateLimiter

TOO_LARGE = "The history or contributor list is too large to list contributors for this repository via the API."


def response(status, body=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result.headers.update(headers or {})
    return result


def install_http(monkeypatch, replies):
    calls = []

    def get(url, *, params, **_kwargs):
        calls.append((url, params))
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr("git_metadata_extractor.providers.github_provider.requests.get", get)
    return calls


def provider():
    return RealGitHubProvider(
        gimie_extractor=lambda _url, _fmt: {}, rate_limiter=RateLimiter(max_retries=0),
    )


@pytest.mark.parametrize("status", [403, 422])
@pytest.mark.parametrize("has_more", [False, True])
def test_exact_oversize_error_yields_one_recent_author_sample(monkeypatch, status, has_more):
    commits = [
        {"author": {"login": "Alice", "type": "User"}, "committer": {"login": "merger"}},
        {"author": {"login": "alice", "type": "User"}},
        {"author": {"login": "bob", "type": "User"}, "contributions": 999},
        {"author": None, "commit": {"author": {"name": "Anonymous"}}},
        {"author": {"login": "lab", "type": "Organization"}},
        {"author": {"login": "not/a/login", "type": "User"}},
        {"author": {"login": "missing-type"}},
        {"author": {"login": "@invalid", "type": "User"}},
        {"author": "bad"},
        None,
    ]
    calls = install_http(monkeypatch, [
        response(status, {"message": TOO_LARGE}),
        response(200, commits, {"Link": '<https://evil.example/steal>; rel="next"'} if has_more else {}),
    ])
    result = provider().get_contributors_with_coverage("owner/repo")
    assert calls == [
        ("https://api.github.com/repos/owner/repo/contributors", {"per_page": 100, "page": 1}),
        ("https://api.github.com/repos/owner/repo/commits", {"per_page": 100, "page": 1}),
    ]
    assert result.contributors == [
        {"login": "Alice", "name": None, "email": None, "contributions": None},
        {"login": "bob", "name": None, "email": None, "contributions": None},
    ]
    assert result.coverage.source == "github_rest_commits"
    assert result.coverage.scope == "recent_commit_authors"
    assert result.coverage.status == "partial"
    assert result.coverage.reason == "recent_commit_sample"
    assert result.coverage.fallback_reason == "github_contributors_too_large"
    assert result.coverage.pages_fetched == 1
    assert result.coverage.returned_count == 2
    warnings = []
    _record_contributor_coverage("owner/repo", result, warnings)
    assert "recent commit sample" in warnings[0]
    assert "incomplete" in warnings[0]


@pytest.mark.parametrize(("status", "message", "headers"), [
    (401, TOO_LARGE, {}),
    (429, TOO_LARGE, {}),
    (500, TOO_LARGE, {}),
    (403, "Forbidden", {}),
    (422, "Validation Failed", {}),
    (403, "API rate limit exceeded", {}),
    (403, TOO_LARGE, {"Retry-After": "0"}),
    (422, TOO_LARGE, {"retry-after": "60"}),
    (403, TOO_LARGE, {"X-RateLimit-Remaining": "0"}),
    (422, TOO_LARGE, {"x-ratelimit-remaining": "0"}),
])
def test_other_errors_and_exhausted_quota_never_route_to_commits(monkeypatch, status, message, headers):
    calls = install_http(monkeypatch, [response(status, {"message": message}, headers)])
    result = provider().get_contributors_with_coverage("owner/repo")
    assert len(calls) == 1
    assert result.coverage.status == "unavailable"
    assert result.contributors == []


@pytest.mark.parametrize("reply", [
    response(200, []), response(204), response(403), response(503),
    response(200, [{"author": None, "committer": {"login": "merger", "type": "User"}}]),
    response(200, {"message": "bad payload"}),
    response(200, [{"author": {"login": "alice", "type": "User"}}] * 101),
    requests.ConnectionError(),
])
def test_failed_or_anonymous_commit_sample_remains_unavailable(monkeypatch, reply):
    calls = install_http(monkeypatch, [response(403, {"message": TOO_LARGE}), reply])
    result = provider().get_contributors_with_coverage("owner/repo")
    assert len(calls) == 2
    assert result.coverage.status == "unavailable"
    assert result.coverage.source == "github_rest_commits"
    assert result.coverage.fallback_reason == "github_contributors_too_large"
    assert result.fetched is False
    assert result.contributors == []


def test_rate_limited_commit_sample_is_not_retried_by_default_limiter(monkeypatch):
    calls = install_http(monkeypatch, [
        response(403, {"message": TOO_LARGE}),
        response(429, {"message": "API rate limit exceeded"}, {"Retry-After": "60"}),
    ])
    github = RealGitHubProvider(gimie_extractor=lambda _url, _fmt: {})
    result = github.get_contributors_with_coverage("owner/repo")
    assert len(calls) == 2
    assert result.coverage.status == "unavailable"
    assert result.coverage.reason == "github_http_429"
