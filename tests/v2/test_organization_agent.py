from __future__ import annotations

import asyncio
from typing import Any, Callable

from jsonschema import validate

from git_metadata_extractor.agents import OrganizationAgentV2, ProviderSet
from git_metadata_extractor.providers.base import GitHubProvider
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider


class _FailingGitHubProvider(GitHubProvider):
    def get_repository(self, full_name: str) -> dict[str, Any]:
        raise AssertionError(full_name)

    def get_user(self, username: str) -> dict[str, Any]:
        raise AssertionError(username)

    def get_organization(self, org_name: str) -> dict[str, Any]:
        raise AssertionError(org_name)

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        raise AssertionError(full_name)

    def get_languages(self, full_name: str) -> dict[str, int]:
        raise AssertionError(full_name)


class _GitHubOrganizationWithRepositoriesProvider(GitHubProvider):
    def get_repository(self, full_name: str) -> dict[str, Any]:
        del full_name
        return {}

    def get_user(self, username: str) -> dict[str, Any]:
        del username
        return {}

    def get_organization(self, org_name: str) -> dict[str, Any]:
        return {
            "login": org_name,
            "name": "GitHub",
            "followers": 200000,
            "repositories": ["repo-a", "repo-b"],
        }

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        del full_name
        return []

    def get_languages(self, full_name: str) -> dict[str, int]:
        del full_name
        return {}


def test_organization_agent_output_validates_against_agent_schema(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    agent = OrganizationAgentV2()
    providers = ProviderSet(
        github=MockGitHubProvider(),
        ror=MockRORProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run(
            {
                "org_name": "github",
                "ror_id": "https://ror.org/02s376052",
            },
            providers,
        ),
    )

    schema = load_schema("agent", "organization")
    validate(instance=result.data, schema=schema)

    assert result.data["schema:name"] == "Ecole Polytechnique Federale de Lausanne"
    assert result.data["pulse:OrganizationType"] == "pulse:University"
    assert result.data["idSource"] == "pulse:ror"
    assert result.data["id"] == "https://ror.org/02s376052"
    assert "schema:alternateName" not in result.data
    assert "source_repositories" not in result.data

    derivation = result.stats.get("derivation")
    assert isinstance(derivation, dict)
    assert derivation["organization_id"] == "https://ror.org/02s376052"
    assert derivation["organization_name"] == "Ecole Polytechnique Federale de Lausanne"
    assert isinstance(derivation["owned_repositories"], list)


def test_organization_agent_falls_back_when_ror_is_unavailable() -> None:
    agent = OrganizationAgentV2()
    providers = ProviderSet(
        github=MockGitHubProvider(),
        ror=MockRORProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run({"org_name": "github", "ror_id": "https://ror.org/000000000"}, providers),
    )

    assert result.warnings
    assert result.data["idSource"] == "pulse:githubOrganizationHandle"
    assert result.data["id"] == "https://github.com/github"


def test_organization_agent_populates_unit_relationships_from_ror_hierarchy() -> None:
    agent = OrganizationAgentV2()
    providers = ProviderSet(
        github=MockGitHubProvider(),
        ror=MockRORProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run({"org_name": "github", "ror_id": "https://ror.org/02s376052"}, providers),
    )

    assert result.data["org:hasUnit"]


def test_organization_agent_permissive_validation_warns_on_malformed_optional_fields() -> None:
    agent = OrganizationAgentV2()
    providers = ProviderSet(
        github=MockGitHubProvider(),
        ror=MockRORProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run(
            {
                "org_name": "github",
                "agent_overrides": {"pulse:OrganizationType": "invalid-type"},
            },
            providers,
        ),
    )

    assert result.warnings
    assert "pulse:OrganizationType" not in result.data
    assert result.raw_output["pulse:OrganizationType"] == "invalid-type"


def test_organization_agent_skips_github_lookup_when_disabled() -> None:
    agent = OrganizationAgentV2()
    providers = ProviderSet(
        github=_FailingGitHubProvider(),
        ror=MockRORProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run(
            {
                "org_name": "EPFL",
                "github_lookup_enabled": False,
                "ror_id": "https://ror.org/02s376052",
            },
            providers,
        ),
    )

    assert result.data["idSource"] == "pulse:ror"
    assert result.data["id"] == "https://ror.org/02s376052"
    assert result.data["identifiers"].get("pulse:githubOrganizationHandle") is None


def test_organization_agent_repository_mode_owns_only_source_repo() -> None:
    agent = OrganizationAgentV2()
    providers = ProviderSet(
        github=_GitHubOrganizationWithRepositoriesProvider(),
        ror=MockRORProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run(
            {
                "org_name": "github",
                "source_repositories": ["owner-org/source-repo"],
            },
            providers,
        ),
    )

    assert result.data["pulse:owns"] == ["owner-org/source-repo"]


def test_organization_agent_surfaces_github_trust_and_activity_signals() -> None:
    """The org agent should pass through `is_verified` (GitHub's
    domain-ownership verification flag), `archived_at`, `public_gists`,
    `following`, and the two `_projects` booleans into the internal
    `_`-prefixed fields. The fixture sets `is_verified=true` so the
    downstream `org_resolver` can short-circuit on a verified org."""
    agent = OrganizationAgentV2()
    providers = ProviderSet(
        github=MockGitHubProvider(),
        ror=MockRORProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run({"org_name": "github"}, providers),
    )

    raw = result.raw_output
    assert raw["_is_verified"] is True
    assert raw["_archived_at"] is None
    assert raw["_public_gists"] == 12
    assert raw["_following_count"] == 0
    assert raw["_has_organization_projects"] is True
    assert raw["_has_repository_projects"] is True
