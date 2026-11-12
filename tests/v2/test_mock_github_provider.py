from __future__ import annotations

from pathlib import Path

import pytest

from git_metadata_extractor.providers.base import (
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from git_metadata_extractor.providers.mock_github import MockGitHubProvider

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "github"
MIN_CONTRIBUTOR_COUNT = 2


@pytest.fixture(scope="module")
def provider() -> MockGitHubProvider:
    return MockGitHubProvider(fixture_root=FIXTURE_ROOT)


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
