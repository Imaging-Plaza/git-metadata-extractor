from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Callable

from jsonschema import validate

from src.v2.agents import ContributionAgentV2, ProviderSet
from src.v2.providers.mock_github import MockGitHubProvider

EXPECTED_CONTRIBUTION_COUNT = 2
EXPECTED_ALICE_CONTRIBUTION_COUNT = 7


def _contribution_context() -> dict[str, Any]:
    return {
        "known_persons": [
            {
                "id": "https://orcid.org/0000-0002-1825-0097",
                "schema:name": "Alice Example",
                "pulse:githubUsername": "alice",
            },
            {
                "id": "https://github.com/bob",
                "schema:name": "Bob Example",
                "pulse:githubUsername": "bob",
            },
        ],
        "known_repositories": [
            {
                "id": "sdsc-ordes/gimie",
                "contributors": [
                    {
                        "login": "alice",
                        "contributions": 5,
                        "firstContributionDate": "2024-01-01T00:00:00Z",
                        "lastContributionDate": "2024-02-01T00:00:00Z",
                    },
                    {
                        "login": "alice",
                        "contributions": 7,
                        "firstContributionDate": "2023-12-01T00:00:00Z",
                        "lastContributionDate": "2024-06-01T00:00:00Z",
                    },
                    {
                        "login": "bob",
                    },
                    {
                        "login": "unknown-contributor",
                        "contributions": 1,
                    },
                ],
            },
        ],
    }


def test_contribution_agent_derives_deterministic_deduplicated_contributions(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    providers = ProviderSet(github=MockGitHubProvider())
    agent = ContributionAgentV2()

    first_result = asyncio.run(agent.run(_contribution_context(), providers))
    second_result = asyncio.run(agent.run(_contribution_context(), providers))

    contributions = first_result.stats["contributions"]
    contribution_schema = load_schema("agent", "contribution")
    for contribution in contributions:
        validate(instance=contribution, schema=contribution_schema)

    assert len(contributions) == EXPECTED_CONTRIBUTION_COUNT
    assert [contribution["id"] for contribution in contributions] == [
        "https://github.com/bob_sdsc-ordes/gimie",
        "https://orcid.org/0000-0002-1825-0097_sdsc-ordes/gimie",
    ]
    assert first_result.stats["contributions"] == second_result.stats["contributions"]


def test_contribution_agent_populates_count_and_nullable_dates() -> None:
    providers = ProviderSet(github=MockGitHubProvider())
    agent = ContributionAgentV2()

    result = asyncio.run(agent.run(_contribution_context(), providers))
    contributions = result.stats["contributions"]
    alice_contribution = next(
        contribution
        for contribution in contributions
        if contribution["schema:author"] == "https://orcid.org/0000-0002-1825-0097"
    )
    bob_contribution = next(
        contribution
        for contribution in contributions
        if contribution["schema:author"] == "https://github.com/bob"
    )

    assert (
        alice_contribution["pulse:contributionCount"]
        == EXPECTED_ALICE_CONTRIBUTION_COUNT
    )
    assert alice_contribution["pulse:firstContributionDate"] == "2023-12-01T00:00:00Z"
    assert alice_contribution["pulse:lastContributionDate"] == "2024-06-01T00:00:00Z"
    assert bob_contribution["pulse:contributionCount"] == 0
    assert bob_contribution["pulse:firstContributionDate"] is None
    assert bob_contribution["pulse:lastContributionDate"] is None
    assert any("Unresolved contribution person mapping" in warning for warning in result.warnings)


def test_contribution_agent_handles_unresolvable_contributors_without_failure() -> None:
    providers = ProviderSet(github=MockGitHubProvider())
    agent = ContributionAgentV2()
    context = deepcopy(_contribution_context())
    context["known_persons"] = []

    result = asyncio.run(agent.run(context, providers))

    assert result.data == {}
    assert result.stats["contributions"] == []
    assert any("Unresolved contribution person mapping" in warning for warning in result.warnings)
