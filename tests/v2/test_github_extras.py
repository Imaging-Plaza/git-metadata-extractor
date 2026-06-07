"""Tests for the GitHub "low-hanging fruit" enrichment:

- FUNDING.yml → funding URLs (pure parser)
- community health profile + git tags (provider methods, mocked requests)
- repository agent stamping funding / community / tags / code-of-conduct fields.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from src.v2.agents import ProviderSet, RepositoryAgentV2
from src.v2.agents.rule_based._repo_signals import parse_funding_urls
from src.v2.ingest.providers.github_provider import RealGitHubProvider
from src.v2.ingest.providers.mock_github import MockGitHubProvider

_EXPECTED_TAGS = 3
_EXPECTED_HEALTH = 80


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


def _provider() -> RealGitHubProvider:
    return RealGitHubProvider(
        gimie_extractor=lambda _url, _fmt: {},
        user_lookup=lambda username: {"login": username},
        organization_lookup=lambda org_name: {"login": org_name},
    )


def _patch_get(response: Any):
    return patch(
        "src.v2.ingest.providers.github_provider.requests.get",
        return_value=response,
    )


# ---------------------------------------------------------------------------
# parse_funding_urls
# ---------------------------------------------------------------------------


def test_parse_funding_urls_all_platforms() -> None:
    funding = (
        "github: [octocat, surftocat]\n"
        "patreon: octo\n"
        "open_collective: myproj\n"
        "ko_fi: octo\n"
        "liberapay: octo\n"
        "buy_me_a_coffee: octo\n"
        'custom: ["https://example.com/donate", "not-a-url"]\n'
    )
    urls = parse_funding_urls({".github/FUNDING.yml": funding})
    assert "https://github.com/sponsors/octocat" in urls
    assert "https://github.com/sponsors/surftocat" in urls
    assert "https://www.patreon.com/octo" in urls
    assert "https://opencollective.com/myproj" in urls
    assert "https://ko-fi.com/octo" in urls
    assert "https://liberapay.com/octo" in urls
    assert "https://www.buymeacoffee.com/octo" in urls
    assert "https://example.com/donate" in urls
    assert "not-a-url" not in urls  # custom non-URL dropped


def test_parse_funding_urls_scalar_github_handle() -> None:
    assert parse_funding_urls({"funding.yml": "github: octocat\n"}) == [
        "https://github.com/sponsors/octocat",
    ]


def test_parse_funding_urls_dedupes_preserving_order() -> None:
    funding = "github: [a, a, b]\n"
    assert parse_funding_urls({"funding.yml": funding}) == [
        "https://github.com/sponsors/a",
        "https://github.com/sponsors/b",
    ]


def test_parse_funding_urls_missing_or_malformed() -> None:
    assert parse_funding_urls({"package.json": "{}"}) == []
    assert parse_funding_urls(None) == []
    assert parse_funding_urls({"funding.yml": "key: [unterminated\n"}) == []
    assert parse_funding_urls({"funding.yml": "just a string"}) == []


# ---------------------------------------------------------------------------
# provider: community profile + tags
# ---------------------------------------------------------------------------


def test_get_repository_community_profile_thins() -> None:
    payload = {
        "health_percentage": 80,
        "documentation": "https://example.com/docs",
        "files": {
            "code_of_conduct": {"name": "Contributor Covenant"},
            "contributing": {"url": "..."},
            "issue_template": None,
            "pull_request_template": {"url": "..."},
        },
    }
    with _patch_get(_FakeResponse(status_code=200, payload=payload)):
        out = _provider().get_repository_community_profile("octocat/Hello-World")
    assert out is not None
    assert out["health_percentage"] == _EXPECTED_HEALTH
    assert out["has_code_of_conduct"] is True
    assert out["has_contributing"] is True
    assert out["has_issue_template"] is False
    assert out["has_pull_request_template"] is True


def test_get_repository_community_profile_404_none() -> None:
    with _patch_get(_FakeResponse(status_code=404)):
        assert _provider().get_repository_community_profile("a/b") is None


def test_get_repository_tags_names() -> None:
    payload = [{"name": "v3.0.0"}, {"name": "v2.0.0"}, {"name": "v1.0.0"}, {"x": 1}]
    with _patch_get(_FakeResponse(status_code=200, payload=payload)):
        tags = _provider().get_repository_tags("octocat/Hello-World")
    assert tags == ["v3.0.0", "v2.0.0", "v1.0.0"]
    assert len(tags) == _EXPECTED_TAGS


def test_get_repository_tags_non_200_empty() -> None:
    with _patch_get(_FakeResponse(status_code=500)):
        assert _provider().get_repository_tags("a/b") == []


# ---------------------------------------------------------------------------
# repository agent emission
# ---------------------------------------------------------------------------


def _stub_context(
    *,
    aux_files: dict[str, str] | None = None,
    community_profile: dict[str, Any] | None = None,
    git_tags: list[str] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": "tool", "full_name": "acme/tool",
        "owner": {"login": "acme", "type": "Organization"},
        "created_at": "2023-01-01T00:00:00Z", "license": {"spdx_id": "MIT"},
        "fork": False, "source": {"full_name": None},
    }
    if community_profile is not None:
        metadata["community_profile"] = community_profile
    if git_tags is not None:
        metadata["git_tags"] = git_tags
    return {
        "full_name": "acme/tool", "metadata": metadata,
        "readme_content": "", "contributors": [{"login": "alice"}],
        "languages": {"Python": 1}, "aux_files": aux_files or {},
    }


def _run(context: dict[str, Any]) -> dict[str, Any]:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(agent.run(
        {"full_name": "acme/tool", "repository_context": context}, providers,
    ))
    return result.raw_output


def test_agent_emits_funding_community_tags_coc() -> None:
    raw = _run(_stub_context(
        aux_files={
            ".github/FUNDING.yml": "github: acme\n",
            "CODE_OF_CONDUCT.md": "# Code of Conduct",
        },
        community_profile={
            "health_percentage": 80,
            "has_code_of_conduct": True,
            "has_issue_template": False,
            "has_pull_request_template": True,
        },
        git_tags=["v2.0.0", "v1.0.0"],
    ))
    assert raw["_funding_urls"] == ["https://github.com/sponsors/acme"]
    assert raw["_code_of_conduct_url"] == (
        "https://github.com/acme/tool/blob/HEAD/CODE_OF_CONDUCT.md"
    )
    assert raw["_community_health_percentage"] == _EXPECTED_HEALTH
    assert raw["_has_code_of_conduct"] is True
    assert raw["_has_issue_template"] is False
    assert raw["_has_pull_request_template"] is True
    assert raw["_git_tags"] == ["v2.0.0", "v1.0.0"]
    assert raw["_git_tag_count"] == len(["v2.0.0", "v1.0.0"])


def test_agent_extras_none_when_absent() -> None:
    raw = _run(_stub_context())
    assert raw["_funding_urls"] is None
    assert raw["_code_of_conduct_url"] is None
    assert raw["_community_health_percentage"] is None
    assert raw["_has_code_of_conduct"] is None
    assert raw["_git_tags"] is None
    assert raw["_git_tag_count"] is None
