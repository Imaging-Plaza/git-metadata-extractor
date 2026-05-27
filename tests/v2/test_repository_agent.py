from __future__ import annotations

import asyncio
from typing import Any, Callable

from jsonschema import validate

from src.v2.agents import ProviderSet, RepositoryAgentV2
from src.v2.ingest.providers.mock_github import MockGitHubProvider


class _NoFetchGitHubProvider(MockGitHubProvider):
    def get_repository(self, full_name: str) -> dict[str, Any]:
        del full_name
        message = "repo_agent should reuse gathered repository metadata"
        raise AssertionError(message)

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        del full_name
        message = "repo_agent should reuse gathered contributor list"
        raise AssertionError(message)

    def get_languages(self, full_name: str) -> dict[str, int]:
        del full_name
        message = "repo_agent should reuse gathered language map"
        raise AssertionError(message)


def test_repository_agent_output_validates_against_agent_schema(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run({"full_name": "octocat/Hello-World"}, providers),
    )

    schema = load_schema("agent", "repository")
    validate(instance=result.data, schema=schema)

    assert result.data["schema:name"] == "Hello-World"
    assert result.data["pulse:githubRepositoryHandle"] == "octocat/Hello-World"
    assert result.data["schema:author"]
    assert result.data["pulse:repositoryType"]
    # `pulse:discipline` is now allowed to be empty when no domain
    # signal applies — the previous `["wd:Q428691"]` catch-all hid
    # honest empty answers behind a noisy default. Octocat's
    # Hello-World fixture has no domain hints, so an empty list is
    # the correct output.
    assert isinstance(result.data["pulse:discipline"], list)
    assert "contributors" not in result.data

    derivation = result.stats.get("derivation")
    assert isinstance(derivation, dict)
    assert derivation["repository_full_name"] == "octocat/Hello-World"
    assert derivation["owner_login"] == "octocat"
    assert "octocat" in derivation["contributor_logins"]


def test_repository_agent_uses_permissive_validation_for_malformed_optional_fields() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "octocat/Hello-World",
                "agent_overrides": {"pulse:repositoryType": "invalid-type"},
            },
            providers,
        ),
    )

    assert result.warnings
    assert "pulse:repositoryType" not in result.data
    assert result.raw_output["pulse:repositoryType"] == "invalid-type"


def test_repository_agent_preserves_raw_output_for_intermediate_debugging() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run({"full_name": "octocat/Hello-World"}, providers),
    )

    assert result.raw_output["schema:name"] == result.data["schema:name"]
    assert result.raw_output["schema:author"] == result.data["schema:author"]


def test_repository_agent_reuses_context_gather_payload_without_provider_refetch(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=_NoFetchGitHubProvider())

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
                        "created_at": "2020-01-01",
                        "license": {"spdx_id": "MIT"},
                        "fork": False,
                        "source": {"full_name": None},
                    },
                    "contributors": [{"login": "octocat"}],
                    "languages": {"Python": 1},
                },
            },
            providers,
        ),
    )

    schema = load_schema("agent", "repository")
    validate(instance=result.data, schema=schema)
    assert result.data["schema:author"] == ["octocat"]
    assert result.data["schema:dateCreated"] == "2020-01-01T00:00:00Z"


def test_repository_agent_filters_organization_contributors_from_authors() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())

    result = asyncio.run(
        agent.run(
            {
                "full_name": "sdsc-ordes/gimie",
                "repository_context": {
                    "full_name": "sdsc-ordes/gimie",
                    "metadata": {
                        "name": "gimie",
                        "full_name": "sdsc-ordes/gimie",
                        "owner": {"login": "sdsc-ordes", "type": "Organization"},
                        "created_at": "2020-01-01T00:00:00Z",
                        "license": {"spdx_id": "MIT"},
                        "fork": False,
                        "source": {"full_name": None},
                    },
                    "contributors": [
                        {"login": "sdsc-ordes", "type": "Organization"},
                        {"login": "alice", "type": "User"},
                    ],
                    "languages": {"Python": 1},
                },
            },
            providers,
        ),
    )

    assert result.data["schema:author"] == ["alice"]
    derivation = result.stats.get("derivation")
    assert isinstance(derivation, dict)
    assert derivation["contributor_logins"] == ["alice"]


def test_repository_agent_emits_aux_file_urls_for_present_files() -> None:
    """When the context-gather slice carries a CITATION.cff / AUTHORS /
    CONTRIBUTING.md / publiccode.yml, the agent should stamp a URL
    pointer for each. The matcher is case-insensitive (real GitHub
    responses preserve the on-disk casing)."""
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
                    },
                    "contributors": [{"login": "octocat"}],
                    "languages": {"Python": 1},
                    "aux_files": {
                        # GitHub preserves the on-disk casing.
                        "CITATION.cff": "cff-version: 1.2.0\nauthors:\n - given-names: Octo\n   family-names: Cat",
                        "AUTHORS.md": "- Octo Cat",
                        "CONTRIBUTING.md": "Open a PR.",
                        "publiccode.yaml": "publiccodeYmlVersion: '0.4'",
                    },
                },
            },
            providers,
        ),
    )

    raw = result.raw_output
    assert raw["_citation_cff_url"] == "https://github.com/octocat/Hello-World/blob/HEAD/CITATION.cff"
    assert raw["_authors_url"] == "https://github.com/octocat/Hello-World/blob/HEAD/AUTHORS.md"
    assert raw["_contributing_url"] == "https://github.com/octocat/Hello-World/blob/HEAD/CONTRIBUTING.md"
    assert raw["_publiccode_url"] == "https://github.com/octocat/Hello-World/blob/HEAD/publiccode.yaml"


def test_repository_agent_emits_none_for_absent_aux_files() -> None:
    """A repo with no CITATION.cff / AUTHORS / CONTRIBUTING / publiccode
    must explicitly carry `None` for those internal fields so downstream
    consumers can distinguish "absent" from "not checked"."""
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
                    },
                    "contributors": [{"login": "octocat"}],
                    "languages": {"Python": 1},
                    "aux_files": {"README.md": "Hello"},  # nothing relevant
                },
            },
            providers,
        ),
    )

    raw = result.raw_output
    assert raw["_citation_cff_url"] is None
    assert raw["_authors_url"] is None
    assert raw["_contributing_url"] is None
    assert raw["_publiccode_url"] is None


def test_repository_agent_parses_publiccode_into_internal_field() -> None:
    """When a publiccode.yml is present in the aux_files, the agent
    must surface the parsed payload under `_publiccode` so downstream
    consumers (LLM refiners, graph dashboards) get typed access to
    license / repoOwner / softwareType / contacts without reparsing."""
    import textwrap
    pcyml = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        name: Hello World Service
        url: https://github.com/octocat/Hello-World
        softwareVersion: 1.0.0
        developmentStatus: stable
        softwareType: standalone/web
        platforms:
          - web
        categories:
          - office
        legal:
          license: MIT
          mainCopyrightOwner: Octo Cat
          repoOwner: Octo Cat
        maintenance:
          type: internal
          contacts:
            - name: Octo Cat
              email: octo@example.com
              affiliation: GitHub
        description:
          en:
            shortDescription: Say hello to the world.
    """)
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
                    },
                    "contributors": [{"login": "octocat"}],
                    "languages": {"Python": 1},
                    "aux_files": {"publiccode.yml": pcyml},
                },
            },
            providers,
        ),
    )

    pc = result.raw_output["_publiccode"]
    assert pc is not None
    assert pc["publiccodeYmlVersion"] == "0.4.0"
    assert pc["name"] == "Hello World Service"
    assert pc["softwareType"] == "standalone/web"
    assert pc["legal"] == {
        "license": "MIT", "mainCopyrightOwner": "Octo Cat", "repoOwner": "Octo Cat",
    }
    assert pc["maintenance"]["contacts"][0]["email"] == "octo@example.com"
    assert pc["description"]["en"]["shortDescription"] == "Say hello to the world."


def test_repository_agent_publiccode_is_none_when_absent() -> None:
    """No publiccode.yml in the aux files → `_publiccode` is None."""
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
                    },
                    "contributors": [{"login": "octocat"}],
                    "languages": {"Python": 1},
                    "aux_files": {"README.md": "Hello"},
                },
            },
            providers,
        ),
    )

    assert result.raw_output["_publiccode"] is None


def test_repository_agent_accepts_alternate_contribution_spelling() -> None:
    """Some projects ship `CONTRIBUTION.md` (singular) — match that too."""
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
                    },
                    "contributors": [{"login": "octocat"}],
                    "languages": {"Python": 1},
                    "aux_files": {"CONTRIBUTION.md": "see docs"},
                },
            },
            providers,
        ),
    )

    assert result.raw_output["_contributing_url"] == (
        "https://github.com/octocat/Hello-World/blob/HEAD/CONTRIBUTION.md"
    )
