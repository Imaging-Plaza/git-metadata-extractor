"""Tests for GitHub releases + GHCR container-image extraction.

Covers `RealGitHubProvider.get_repository_releases` /
`get_repository_container_images` (thinning, pagination scope, the
org→user fallback, and graceful degradation when the token lacks
`read:packages`) and the repository agent stamping the results onto the
`_releases` / `_container_images` internal fields.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from git_metadata_extractor.agents import ProviderSet, RepositoryAgentV2
from git_metadata_extractor.providers.github_provider import RealGitHubProvider
from git_metadata_extractor.providers.mock_github import MockGitHubProvider


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: Any = None, raise_json: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self) -> Any:
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


def _build_provider() -> RealGitHubProvider:
    return RealGitHubProvider(
        gimie_extractor=lambda _url, _fmt: {},
        user_lookup=lambda username: {"login": username},
        organization_lookup=lambda org_name: {"login": org_name},
    )


def _patch_get(*responses_or_side_effect: Any, side_effect: Any = None):
    target = "git_metadata_extractor.providers.github_provider.requests.get"
    if side_effect is not None:
        return patch(target, side_effect=side_effect)
    if len(responses_or_side_effect) == 1:
        return patch(target, return_value=responses_or_side_effect[0])
    return patch(target, side_effect=list(responses_or_side_effect))


# --------------------------------------------------------------------------
# Releases
# --------------------------------------------------------------------------


def test_get_repository_releases_thins_payload() -> None:
    provider = _build_provider()
    payload = [
        {
            "tag_name": "v2.0.0",
            "name": "Release 2.0.0",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-01-02T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/o/r/releases/tag/v2.0.0",
            "tarball_url": "https://api.github.com/repos/o/r/tarball/v2.0.0",
            "zipball_url": "https://api.github.com/repos/o/r/zipball/v2.0.0",
            "assets": [
                {
                    "name": "wheel.whl",
                    "browser_download_url": "https://github.com/o/r/releases/download/v2.0.0/wheel.whl",
                    "content_type": "application/octet-stream",
                    "size": 1234,
                    "download_count": 9,
                    "uploader": {"login": "noise"},  # dropped by thinning
                },
            ],
            "body": "noise dropped",  # not in thinned shape
        },
    ]
    with _patch_get(_FakeResponse(status_code=200, payload=payload)):
        out = provider.get_repository_releases("o/r")

    assert len(out) == 1
    rel = out[0]
    assert rel["tag_name"] == "v2.0.0"
    assert rel["prerelease"] is False
    assert "body" not in rel
    assert rel["assets"][0]["download_count"] == 9
    assert "uploader" not in rel["assets"][0]


@pytest.mark.parametrize("status_code", [404, 500, 403])
def test_get_repository_releases_empty_on_http_error(status_code: int) -> None:
    provider = _build_provider()
    with _patch_get(_FakeResponse(status_code=status_code, payload={"message": "x"})):
        assert provider.get_repository_releases("o/r") == []


def test_get_repository_releases_empty_when_not_json() -> None:
    provider = _build_provider()
    with _patch_get(_FakeResponse(status_code=200, raise_json=True)):
        assert provider.get_repository_releases("o/r") == []


def test_get_repository_releases_empty_when_request_raises() -> None:
    provider = _build_provider()
    with _patch_get(side_effect=ConnectionError("boom")):
        assert provider.get_repository_releases("o/r") == []


def test_get_repository_releases_empty_when_payload_not_list() -> None:
    provider = _build_provider()
    with _patch_get(_FakeResponse(status_code=200, payload={"message": "Not Found"})):
        assert provider.get_repository_releases("o/r") == []


# --------------------------------------------------------------------------
# Container images
# --------------------------------------------------------------------------


def test_container_images_matches_by_link_and_name_with_tags() -> None:
    provider = _build_provider()
    packages = [
        # name-convention match (package name == repo short name)
        {"name": "Hello-World", "visibility": "public",
         "repository": None, "html_url": "https://github.com/octocat/Hello-World/pkgs/container/Hello-World"},
        # repository-link match (explicit source linkage)
        {"name": "worker", "visibility": "private",
         "repository": {"full_name": "octocat/Hello-World"}, "html_url": "x"},
        # unrelated → dropped
        {"name": "other", "repository": {"full_name": "octocat/other"}},
    ]
    versions_helloworld = [
        {"metadata": {"container": {"tags": ["latest", "1.0.0"]}}},
        {"metadata": {"container": {"tags": ["1.0.0", "sha-abc"]}}},  # dup 1.0.0 deduped
    ]
    versions_worker = [{"metadata": {"container": {"tags": ["edge"]}}}]

    with _patch_get(
        _FakeResponse(status_code=200, payload=packages),     # orgs listing
        _FakeResponse(status_code=200, payload=versions_helloworld),
        _FakeResponse(status_code=200, payload=versions_worker),
    ):
        out = provider.get_repository_container_images("octocat/Hello-World")

    assert [i["name"] for i in out] == ["Hello-World", "worker"]
    hw = out[0]
    assert hw["image"] == "ghcr.io/octocat/Hello-World"
    assert hw["match"] == "name_convention"
    assert hw["tags"] == ["latest", "1.0.0", "sha-abc"]  # ordered + deduped
    worker = out[1]
    assert worker["match"] == "repository"
    assert worker["linked_repository"] == "octocat/Hello-World"
    assert worker["tags"] == ["edge"]


def test_container_images_empty_without_packages_scope() -> None:
    """401/403 on the owner listing (token lacks read:packages) → []."""
    provider = _build_provider()
    with _patch_get(_FakeResponse(status_code=401, payload={"message": "needs read:packages"})):
        assert provider.get_repository_container_images("octocat/Hello-World") == []


def test_container_images_falls_back_to_user_endpoint_on_404() -> None:
    """orgs/{owner} 404 (owner is a user) → retry users/{owner}."""
    provider = _build_provider()
    packages = [{"name": "Hello-World", "repository": {"full_name": "octocat/Hello-World"}}]
    versions = [{"metadata": {"container": {"tags": ["v1"]}}}]
    with _patch_get(
        _FakeResponse(status_code=404, payload={"message": "Not Found"}),  # orgs → miss
        _FakeResponse(status_code=200, payload=packages),                  # users → hit
        _FakeResponse(status_code=200, payload=versions),                  # versions
    ):
        out = provider.get_repository_container_images("octocat/Hello-World")
    assert len(out) == 1
    assert out[0]["tags"] == ["v1"]


def test_container_images_empty_when_no_match() -> None:
    provider = _build_provider()
    packages = [{"name": "totally-different", "repository": {"full_name": "someone/else"}}]
    with _patch_get(_FakeResponse(status_code=200, payload=packages)):
        assert provider.get_repository_container_images("octocat/Hello-World") == []


def test_container_images_empty_for_bad_full_name() -> None:
    provider = _build_provider()
    # No request should be issued for a handle without an owner/repo split.
    with _patch_get(side_effect=AssertionError("should not call the API")):
        assert provider.get_repository_container_images("no-slash") == []


# --------------------------------------------------------------------------
# Agent stamping
# --------------------------------------------------------------------------


def _run_agent_with_metadata(metadata_extra: dict[str, Any]) -> dict[str, Any]:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(
        agent.run(
            {
                "full_name": "octocat/Hello-World",
                "repository_context": {
                    "full_name": "octocat/Hello-World",
                    "metadata": {
                        "name": "Hello-World",
                        "full_name": "octocat/Hello-World",
                        "owner": {"login": "octocat", "type": "User"},
                        "created_at": "2020-01-01T00:00:00Z",
                        "license": {"spdx_id": "MIT"},
                        "fork": False,
                        "source": {"full_name": None},
                        **metadata_extra,
                    },
                    "contributors": [{"login": "octocat"}],
                    "languages": {"Python": 1},
                    "aux_files": {},
                },
            },
            providers,
        ),
    )
    return result.raw_output


def test_agent_stamps_releases_and_container_images() -> None:
    releases = [{"tag_name": "v1.0.0", "name": "1.0.0", "assets": []}]
    images = [{"name": "Hello-World", "image": "ghcr.io/octocat/Hello-World", "tags": ["latest"]}]
    raw = _run_agent_with_metadata({"releases": releases, "container_images": images})
    assert raw["_releases"] == releases
    assert raw["_container_images"] == images


def test_agent_releases_and_images_none_when_absent() -> None:
    raw = _run_agent_with_metadata({})
    assert raw["_releases"] is None
    assert raw["_container_images"] is None
