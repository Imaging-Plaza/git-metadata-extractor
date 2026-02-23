from __future__ import annotations

import asyncio
from typing import Any, Callable

from jsonschema import validate

from src.v2.agents import PersonAgentV2, ProviderSet
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_orcid import MockORCIDProvider

MIN_EXPECTED_MEMBERSHIPS = 2
INVALID_ORCID_FIELD_VALUE = 123


def test_person_agent_output_validates_and_merges_affiliations(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    agent = PersonAgentV2()
    providers = ProviderSet(
        github=MockGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run(
            {
                "username": "octocat",
                "email": "alice@example.org",
                "orcid": "0000-0002-1825-0097",
                "person_query": "alice smith",
            },
            providers,
        ),
    )

    schema = load_schema("agent", "person")
    validate(instance=result.data, schema=schema)

    assert result.data["schema:name"] == "Alice Example"
    assert result.data["pulse:githubUsername"] == "octocat"
    assert result.data["schema:email"] == "a***@example.org"
    assert len(result.data["org:hasMembership"]) >= MIN_EXPECTED_MEMBERSHIPS


def test_person_agent_warns_and_falls_back_when_orcid_record_is_unavailable() -> None:
    agent = PersonAgentV2()
    providers = ProviderSet(
        github=MockGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run(
            {
                "username": "octocat",
                "orcid": "7913-0249-0843-820X",
            },
            providers,
        ),
    )

    assert result.warnings
    assert result.data["idSource"] == "pulse:githubUsername"
    assert result.data["pulse:githubUsername"] == "octocat"


def test_person_agent_permissive_validation_warns_without_raising() -> None:
    agent = PersonAgentV2()
    providers = ProviderSet(
        github=MockGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
    )

    result = asyncio.run(
        agent.run(
            {
                "username": "octocat",
                "agent_overrides": {"pulse:orcidIdentifier": INVALID_ORCID_FIELD_VALUE},
            },
            providers,
        ),
    )

    assert result.warnings
    assert "pulse:orcidIdentifier" not in result.data
    assert result.raw_output["pulse:orcidIdentifier"] == INVALID_ORCID_FIELD_VALUE
