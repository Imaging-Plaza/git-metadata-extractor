"""Tests for v2 repository enrichment fields (gme-internal).

Covers:
  - parse_test_coverage: static shields badge, plain text, dynamic badge (None), empty (None)
  - parse_docker_hub_url: direct URL, docker pull, no ref, official single-name image
  - detect_has_ci: root listing with .github → True; README-only → False; .gitlab-ci.yml → True
  - entity-level: RepositoryAgentV2 emits _has_ci, _test_coverage, _docker_hub_url,
    _latest_version, and _releases when stub context carries the right data.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents import ProviderSet, RepositoryAgentV2
from src.v2.agents.rule_based._repo_signals import (
    detect_has_ci,
    parse_docker_hub_url,
    parse_test_coverage,
    summarize_releases,
)
from src.v2.ingest.providers.mock_github import MockGitHubProvider

# ---------------------------------------------------------------------------
# parse_test_coverage
# ---------------------------------------------------------------------------


def test_parse_test_coverage_static_shields_badge_percent25() -> None:
    readme = "![cov](https://img.shields.io/badge/coverage-87%25-green)"
    assert parse_test_coverage(readme) == "87%"


def test_parse_test_coverage_static_shields_badge_percent_literal() -> None:
    readme = "![cov](https://img.shields.io/badge/coverage-92%-brightgreen)"
    assert parse_test_coverage(readme) == "92%"


def test_parse_test_coverage_plain_text_coverage_colon() -> None:
    readme = "The project has coverage: 92% according to the CI report."
    assert parse_test_coverage(readme) == "92%"


def test_parse_test_coverage_plain_text_test_coverage() -> None:
    readme = "test coverage: 75%\nSee the CI dashboard for details."
    assert parse_test_coverage(readme) == "75%"


def test_parse_test_coverage_dynamic_codecov_badge_returns_none() -> None:
    # Dynamic badge — no number embedded in the README text
    readme = (
        "[![codecov](https://codecov.io/gh/owner/repo/branch/main/graph/badge.svg)]"
        "(https://codecov.io/gh/owner/repo)"
    )
    assert parse_test_coverage(readme) is None


def test_parse_test_coverage_empty_returns_none() -> None:
    assert parse_test_coverage("") is None


def test_parse_test_coverage_none_returns_none() -> None:
    assert parse_test_coverage(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_docker_hub_url
# ---------------------------------------------------------------------------


def test_parse_docker_hub_url_direct_hub_link() -> None:
    readme = "See https://hub.docker.com/r/acme/tool for the image."
    assert parse_docker_hub_url(readme, None) == "https://hub.docker.com/r/acme/tool"


def test_parse_docker_hub_url_docker_pull() -> None:
    readme = "Install with `docker pull acme/tool`."
    assert parse_docker_hub_url(readme, None) == "https://hub.docker.com/r/acme/tool"


def test_parse_docker_hub_url_no_docker_ref_returns_none() -> None:
    readme = "No docker info here. Just plain text."
    assert parse_docker_hub_url(readme, None) is None


def test_parse_docker_hub_url_official_single_name_returns_none() -> None:
    # Official images (no namespace) must NOT produce a URL
    readme = "Run `docker pull python` to get started."
    assert parse_docker_hub_url(readme, None) is None


def test_parse_docker_hub_url_docker_run_image_ref() -> None:
    readme = "Example: docker run acme/tool:latest"
    assert parse_docker_hub_url(readme, None) == "https://hub.docker.com/r/acme/tool"


def test_parse_docker_hub_url_strips_tag() -> None:
    readme = "docker pull acme/my-image:v1.2.3"
    assert parse_docker_hub_url(readme, None) == "https://hub.docker.com/r/acme/my-image"


def test_parse_docker_hub_url_compose_image_line() -> None:
    aux_files = {"docker-compose.yml": "services:\n  app:\n    image: acme/tool:latest\n"}
    assert parse_docker_hub_url("", aux_files) == "https://hub.docker.com/r/acme/tool"


def test_parse_docker_hub_url_empty_returns_none() -> None:
    assert parse_docker_hub_url("", None) is None


# ---------------------------------------------------------------------------
# detect_has_ci
# ---------------------------------------------------------------------------


def test_detect_has_ci_github_actions_dir() -> None:
    assert detect_has_ci([".github", "README.md", "src"]) is True


def test_detect_has_ci_readme_only() -> None:
    assert detect_has_ci(["README.md", "src", "setup.py"]) is False


def test_detect_has_ci_gitlab_ci_yml() -> None:
    assert detect_has_ci([".gitlab-ci.yml", "README.md"]) is True


def test_detect_has_ci_travis_yml() -> None:
    assert detect_has_ci([".travis.yml"]) is True


def test_detect_has_ci_circleci_dir() -> None:
    assert detect_has_ci([".circleci", "README.md"]) is True


def test_detect_has_ci_azure_pipelines() -> None:
    assert detect_has_ci(["azure-pipelines.yml", "README.md"]) is True


def test_detect_has_ci_jenkinsfile() -> None:
    assert detect_has_ci(["Jenkinsfile"]) is True


def test_detect_has_ci_drone_yml() -> None:
    assert detect_has_ci([".drone.yml"]) is True


def test_detect_has_ci_bitbucket_pipelines() -> None:
    assert detect_has_ci(["bitbucket-pipelines.yml"]) is True


def test_detect_has_ci_woodpecker_yml() -> None:
    assert detect_has_ci([".woodpecker.yml"]) is True


def test_detect_has_ci_case_insensitive() -> None:
    assert detect_has_ci([".TRAVIS.YML"]) is True


def test_detect_has_ci_none_returns_none() -> None:
    assert detect_has_ci(None) is None


def test_detect_has_ci_empty_list_returns_false() -> None:
    assert detect_has_ci([]) is False


# ---------------------------------------------------------------------------
# Entity-level test: RepositoryAgentV2 emits the 4 new internal fields
# ---------------------------------------------------------------------------

_STUB_RELEASES = [
    {
        "tag_name": "v2.1.0",
        "name": "Version 2.1.0",
        "published_at": "2024-03-01T12:00:00Z",
        "html_url": "https://github.com/acme/tool/releases/tag/v2.1.0",
    },
    {
        "tag_name": "v2.0.0",
        "name": "Version 2.0.0",
        "published_at": "2024-01-15T08:00:00Z",
        "html_url": "https://github.com/acme/tool/releases/tag/v2.0.0",
    },
]

_README_WITH_SIGNALS = (
    "# My Tool\n"
    "![coverage](https://img.shields.io/badge/coverage-87%25-green)\n"
    "Pull image: docker pull acme/tool\n"
    "Great software!\n"
)


def _make_stub_context(
    *,
    releases: list[dict[str, Any]] | None = None,
    readme_content: str = "",
    has_ci: bool | None = None,
    aux_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a repository_context dict as the orchestrator/gather_context would."""
    metadata: dict[str, Any] = {
        "name": "tool",
        "full_name": "acme/tool",
        "owner": {"login": "acme", "type": "Organization"},
        "created_at": "2023-01-01T00:00:00Z",
        "license": {"spdx_id": "MIT"},
        "fork": False,
        "source": {"full_name": None},
    }
    if releases is not None:
        metadata["releases"] = releases
    if has_ci is not None:
        metadata["has_ci"] = has_ci

    return {
        "full_name": "acme/tool",
        "metadata": metadata,
        "readme_content": readme_content,
        "contributors": [{"login": "alice"}],
        "languages": {"Python": 1},
        "aux_files": aux_files or {},
    }


def test_repository_agent_emits_releases_and_latest_version() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(releases=_STUB_RELEASES),
            },
            providers,
        ),
    )

    raw = result.raw_output
    assert raw["_latest_version"] == "v2.1.0"
    releases = raw["_releases"]
    assert isinstance(releases, list)
    assert len(releases) == len(_STUB_RELEASES)
    # _releases is the raw GitHub releases payload (unchanged contract);
    # _latest_version is the derived addition.
    first = releases[0]
    assert first["tag_name"] == "v2.1.0"
    assert first["name"] == "Version 2.1.0"
    assert first["published_at"] == "2024-03-01T12:00:00Z"
    assert first["html_url"] == "https://github.com/acme/tool/releases/tag/v2.1.0"


def test_repository_agent_releases_none_when_absent() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(releases=None),
            },
            providers,
        ),
    )

    raw = result.raw_output
    assert raw["_latest_version"] is None
    assert raw["_releases"] is None
    # Flat release scalars are always present, None when no releases.
    assert raw["_release_count"] is None
    assert raw["_first_release_date"] is None
    assert raw["_latest_release_date"] is None


# ---------------------------------------------------------------------------
# summarize_releases — flat release scalars for "Release Frequency"
# ---------------------------------------------------------------------------


def test_summarize_releases_counts_and_date_bounds() -> None:
    out = summarize_releases(_STUB_RELEASES)
    assert out["release_count"] == len(_STUB_RELEASES)
    # Bounds are min/max over published_at (independent of list order).
    assert out["first_release_date"] == "2024-01-15T08:00:00Z"
    assert out["latest_release_date"] == "2024-03-01T12:00:00Z"


def test_summarize_releases_none_input_all_none() -> None:
    out = summarize_releases(None)
    assert out == {
        "release_count": None,
        "first_release_date": None,
        "latest_release_date": None,
    }


def test_summarize_releases_empty_list_counts_zero_no_dates() -> None:
    out = summarize_releases([])
    assert out["release_count"] == 0
    assert out["first_release_date"] is None
    assert out["latest_release_date"] is None


def test_summarize_releases_ignores_missing_published_at_for_bounds() -> None:
    releases = [
        {"tag_name": "v3", "published_at": "2024-05-01T00:00:00Z"},
        {"tag_name": "draft"},  # no published_at → counted, ignored for bounds
        {"tag_name": "v1", "published_at": "2024-02-01T00:00:00Z"},
    ]
    out = summarize_releases(releases)
    assert out["release_count"] == len(releases)
    assert out["first_release_date"] == "2024-02-01T00:00:00Z"
    assert out["latest_release_date"] == "2024-05-01T00:00:00Z"


def test_repository_agent_emits_flat_release_scalars() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(releases=_STUB_RELEASES),
            },
            providers,
        ),
    )

    raw = result.raw_output
    assert raw["_release_count"] == len(_STUB_RELEASES)
    assert raw["_first_release_date"] == "2024-01-15T08:00:00Z"
    assert raw["_latest_release_date"] == "2024-03-01T12:00:00Z"


def test_repository_agent_emits_test_coverage_from_readme() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(
                    readme_content=_README_WITH_SIGNALS,
                ),
            },
            providers,
        ),
    )

    assert result.raw_output["_test_coverage"] == "87%"


def test_repository_agent_test_coverage_none_when_no_badge() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(readme_content="# No coverage here"),
            },
            providers,
        ),
    )

    assert result.raw_output["_test_coverage"] is None


def test_repository_agent_emits_docker_hub_url_from_readme() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(
                    readme_content=_README_WITH_SIGNALS,
                ),
            },
            providers,
        ),
    )

    assert result.raw_output["_docker_hub_url"] == "https://hub.docker.com/r/acme/tool"


def test_repository_agent_docker_hub_url_none_when_absent() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(readme_content="# No docker here"),
            },
            providers,
        ),
    )

    assert result.raw_output["_docker_hub_url"] is None


def test_repository_agent_emits_has_ci_true() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(has_ci=True),
            },
            providers,
        ),
    )

    assert result.raw_output["_has_ci"] is True


def test_repository_agent_emits_has_ci_false() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(has_ci=False),
            },
            providers,
        ),
    )

    assert result.raw_output["_has_ci"] is False


def test_repository_agent_has_ci_none_when_not_in_metadata() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(),
            },
            providers,
        ),
    )

    # has_ci not set in metadata → should be None
    assert result.raw_output["_has_ci"] is None


def test_repository_agent_all_enrichment_fields_combined() -> None:
    """Smoke test: all 4 enrichment fields present in a single run."""
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_stub_context(
                    releases=_STUB_RELEASES,
                    readme_content=_README_WITH_SIGNALS,
                    has_ci=True,
                ),
            },
            providers,
        ),
    )

    raw = result.raw_output
    assert raw["_has_ci"] is True
    assert raw["_test_coverage"] == "87%"
    assert raw["_docker_hub_url"] == "https://hub.docker.com/r/acme/tool"
    assert raw["_latest_version"] == "v2.1.0"
    assert isinstance(raw["_releases"], list)
    assert len(raw["_releases"]) == len(_STUB_RELEASES)
