from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.v2.agents import ProviderSet
from src.v2.detection.models import GitHubURLClassification, GitHubURLType
from src.v2.pipeline.stages import gather_context
from src.v2.pipeline.stages.context_gather import RequiredProviderUnavailableError
from src.v2.providers.base import GitHubProvider, ORCIDProvider, ORCIDRecord

EXPECTED_CONTRIBUTOR_COUNT = 2


class _DummyGitHubProvider(GitHubProvider):
    def get_repository(self, full_name: str) -> dict[str, Any]:
        return {
            "full_name": full_name,
            "readme_content": "# Hello World",
            "description": "Hello world repository",
            "owner": {"login": "octocat", "type": "User"},
        }

    def get_user(self, username: str) -> dict[str, Any]:
        return {
            "login": username,
            "name": "Alice Example",
            "orcid": "0000-0002-1825-0097",
            "repositories": [f"{username}/repo-a", "repo-b"],
        }

    def get_organization(self, org_name: str) -> dict[str, Any]:
        return {
            "login": org_name,
            "name": "Org Example",
            "members": ["alice", "bob"],
            "repositories": [f"{org_name}/repo-a", f"{org_name}/repo-b"],
        }

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        del full_name
        return [{"login": "alice"}, {"login": "bob"}]

    def get_languages(self, full_name: str) -> dict[str, int]:
        del full_name
        return {"Python": 42}


class _DummyORCIDProvider(ORCIDProvider):
    def get_person_by_orcid(self, orcid_id: str) -> ORCIDRecord:
        return ORCIDRecord(
            orcid_id=orcid_id,
            name="Alice Example",
            employment=[],
            education=[],
            affiliations=["EPFL"],
        )


def test_repository_context_contains_expected_sections() -> None:
    providers = ProviderSet(github=_DummyGitHubProvider())
    url_info = GitHubURLClassification(
        normalized_url="https://github.com/octocat/Hello-World",
        detected_type=GitHubURLType.REPOSITORY,
        owner="octocat",
        repo="Hello-World",
    )

    bundle = asyncio.run(gather_context("repository", url_info, providers))

    repository_context = bundle.context["repository"]
    assert repository_context["metadata"]["full_name"] == "octocat/Hello-World"
    assert repository_context["readme_content"] == "# Hello World"
    assert len(repository_context["contributors"]) == EXPECTED_CONTRIBUTOR_COUNT
    assert "Python" in repository_context["languages"]


def test_user_context_contains_profile_repos_and_orcid() -> None:
    providers = ProviderSet(
        github=_DummyGitHubProvider(),
        orcid=_DummyORCIDProvider(),
    )
    url_info = GitHubURLClassification(
        normalized_url="https://github.com/alice",
        detected_type=GitHubURLType.USER,
        owner="alice",
        repo=None,
    )

    bundle = asyncio.run(gather_context("user", url_info, providers))

    user_context = bundle.context["user"]
    assert user_context["profile"]["login"] == "alice"
    assert user_context["owned_repos"] == ["alice/repo-a", "repo-b"]
    assert set(user_context["repository_contexts"]) == {"alice/repo-a", "alice/repo-b"}
    assert user_context["orcid_data"]["name"] == "Alice Example"


def test_org_context_contains_profile_members_and_repositories() -> None:
    providers = ProviderSet(github=_DummyGitHubProvider())
    url_info = GitHubURLClassification(
        normalized_url="https://github.com/orgs/example",
        detected_type=GitHubURLType.ORGANIZATION,
        owner="example",
        repo=None,
    )

    bundle = asyncio.run(gather_context("organization", url_info, providers))

    organization_context = bundle.context["organization"]
    assert organization_context["profile"]["login"] == "example"
    assert organization_context["members"] == ["alice", "bob"]
    assert organization_context["owned_repos"] == ["example/repo-a", "example/repo-b"]
    assert set(organization_context["repository_contexts"]) == {
        "example/repo-a",
        "example/repo-b",
    }


def test_optional_repository_context_failures_are_warnings_for_user_mode() -> None:
    class _PartiallyFailingGitHubProvider(_DummyGitHubProvider):
        def get_repository(self, full_name: str) -> dict[str, Any]:
            if full_name.endswith("repo-b"):
                raise RuntimeError("repo unavailable")
            return super().get_repository(full_name)

    providers = ProviderSet(
        github=_PartiallyFailingGitHubProvider(),
        orcid=_DummyORCIDProvider(),
    )
    url_info = GitHubURLClassification(
        normalized_url="https://github.com/alice",
        detected_type=GitHubURLType.USER,
        owner="alice",
        repo=None,
    )

    bundle = asyncio.run(gather_context("user", url_info, providers))

    user_context = bundle.context["user"]
    assert set(user_context["repository_contexts"]) == {"alice/repo-a"}
    assert any(
        "Repository metadata lookup failed for alice/repo-b" in warning
        for warning in bundle.warnings
    )


def test_missing_optional_orcid_adds_warning_instead_of_error() -> None:
    class _NoOrcidGitHubProvider(_DummyGitHubProvider):
        def get_user(self, username: str) -> dict[str, Any]:
            payload = super().get_user(username)
            payload.pop("orcid", None)
            return payload

    providers = ProviderSet(github=_NoOrcidGitHubProvider())
    url_info = GitHubURLClassification(
        normalized_url="https://github.com/alice",
        detected_type=GitHubURLType.USER,
        owner="alice",
        repo=None,
    )

    bundle = asyncio.run(gather_context("user", url_info, providers))

    assert any("ORCID data unavailable" in warning for warning in bundle.warnings)


def test_context_bundle_is_serializable() -> None:
    providers = ProviderSet(github=_DummyGitHubProvider())
    url_info = GitHubURLClassification(
        normalized_url="https://github.com/octocat/Hello-World",
        detected_type=GitHubURLType.REPOSITORY,
        owner="octocat",
        repo="Hello-World",
    )

    bundle = asyncio.run(gather_context("repository", url_info, providers))
    serialized = bundle.to_dict()

    assert serialized["detected_type"] == "repository"
    json.dumps(serialized)


def test_repository_context_raises_when_required_github_call_fails() -> None:
    class _FailingGitHubProvider(_DummyGitHubProvider):
        def get_repository(self, full_name: str) -> dict[str, Any]:
            del full_name
            raise RuntimeError("github unavailable")

    providers = ProviderSet(github=_FailingGitHubProvider())
    url_info = GitHubURLClassification(
        normalized_url="https://github.com/octocat/Hello-World",
        detected_type=GitHubURLType.REPOSITORY,
        owner="octocat",
        repo="Hello-World",
    )

    with pytest.raises(RequiredProviderUnavailableError, match="repository metadata lookup"):
        asyncio.run(gather_context("repository", url_info, providers))
