from __future__ import annotations

import asyncio
from typing import Any, Callable

from jsonschema import validate

from src.v2.agents import ProviderSet, RepositoryAgentV2
from src.v2.providers.mock_github import MockGitHubProvider


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
    assert result.data["pulse:discipline"]


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
