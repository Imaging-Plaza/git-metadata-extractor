from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from src.v2.ingest.providers.base import (
    GitHubProvider,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from src.v2.ingest.providers.mock_github import MockGitHubProvider

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "github"
MIN_CONTRIBUTOR_COUNT = 2
MIN_FIXTURE_FILE_COUNT = 6
EXPECTED_FIXTURE_FILES = {
    "contributors_payload.json",
    "not_found_response.json",
    "org_payload.json",
    "rate_limited_response.json",
    "repo_payload.json",
    "user_payload.json",
}


@pytest.fixture(scope="module")
def provider() -> MockGitHubProvider:
    return MockGitHubProvider(fixture_root=FIXTURE_ROOT)


def test_mock_github_provider_implements_base_interface(
    provider: MockGitHubProvider,
) -> None:
    assert isinstance(provider, GitHubProvider)
    assert not MockGitHubProvider.__abstractmethods__


def test_get_repository_returns_expected_rest_payload_shape(
    provider: MockGitHubProvider,
) -> None:
    repository = provider.get_repository("octocat/Hello-World")

    assert repository["full_name"] == "octocat/Hello-World"
    assert repository["owner"]["login"] == "octocat"
    assert repository["private"] is False
    assert repository["topics"] == ["metadata", "ai"]
    assert repository["license"]["spdx_id"] == "MIT"
    assert "languages_url" in repository
    assert "contributors_url" in repository


def test_mock_provider_supports_user_org_contributor_and_language_payloads(
    provider: MockGitHubProvider,
) -> None:
    user = provider.get_user("octocat")
    organization = provider.get_organization("github")
    contributors = provider.get_contributors("octocat/Hello-World")
    languages = provider.get_languages("octocat/Hello-World")

    assert user["login"] == "octocat"
    assert organization["login"] == "github"
    assert len(contributors) >= MIN_CONTRIBUTOR_COUNT
    assert contributors[0]["login"] == "octocat"
    assert languages["Python"] > 0


def test_repository_error_cases_raise_typed_provider_errors(
    provider: MockGitHubProvider,
) -> None:
    with pytest.raises(ProviderNotFoundError):
        provider.get_repository("missing/repo")

    with pytest.raises(ProviderRateLimitError):
        provider.get_repository("rate-limited/repo")

    with pytest.raises(ProviderPermissionError):
        provider.get_repository("private/repo")


def test_github_fixture_catalog_contains_required_files() -> None:
    fixture_files = {path.name for path in FIXTURE_ROOT.glob("*.json")}

    assert EXPECTED_FIXTURE_FILES.issubset(fixture_files)
    assert len(fixture_files) >= MIN_FIXTURE_FILE_COUNT


def test_github_fixture_files_are_valid_json() -> None:
    for fixture_path in sorted(FIXTURE_ROOT.glob("*.json")):
        with fixture_path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)

        assert isinstance(payload, dict), (
            f"Expected JSON object fixture: {fixture_path.name}"
        )


def test_repo_and_contributor_fixtures_include_rest_and_graphql_shapes(
    load_fixture: Callable[[str, str], Any],
) -> None:
    repository_payload = load_fixture("providers/github", "repo_payload")
    contributors_payload = load_fixture("providers/github", "contributors_payload")

    assert "rest" in repository_payload
    assert "graphql" in repository_payload
    assert "languages" in repository_payload

    assert "rest" in contributors_payload
    assert "graphql" in contributors_payload
