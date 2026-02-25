from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Callable

from jsonschema import validate

from src.v2.agents import MembershipAgentV2, ProviderSet
from src.v2.providers.mock_github import MockGitHubProvider

EXPECTED_MEMBERSHIP_COUNT = 2


def _membership_context() -> dict[str, Any]:
    return {
        "known_persons": [
            {
                "id": "https://orcid.org/0000-0002-1825-0097",
                "schema:name": "Alice Example",
                "affiliations": [
                    "EPFL",
                    "EPFL",
                    "University of Lausanne",
                ],
                "orcid_affiliations": [
                    {
                        "organization": "EPFL",
                        "role": "Research Engineer",
                        "start_date": "2021-01-01",
                        "end_date": None,
                    },
                ],
            },
        ],
        "known_organizations": [
            {
                "id": "https://ror.org/02s376052",
                "schema:name": "EPFL",
            },
            {
                "id": "https://ror.org/019wvm592",
                "schema:name": "University of Lausanne",
            },
        ],
    }


def test_membership_agent_derives_deterministic_deduplicated_memberships(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    providers = ProviderSet(github=MockGitHubProvider())
    agent = MembershipAgentV2()

    first_result = asyncio.run(agent.run(_membership_context(), providers))
    second_result = asyncio.run(agent.run(_membership_context(), providers))

    memberships = first_result.stats["memberships"]
    membership_schema = load_schema("agent", "membership")
    for membership in memberships:
        validate(instance=membership, schema=membership_schema)

    assert len(memberships) == EXPECTED_MEMBERSHIP_COUNT
    assert [membership["id"] for membership in memberships] == [
        "https://orcid.org/0000-0002-1825-0097_https://ror.org/019wvm592",
        "https://orcid.org/0000-0002-1825-0097_https://ror.org/02s376052",
    ]
    assert first_result.stats["memberships"] == second_result.stats["memberships"]


def test_membership_agent_enriches_role_and_dates_from_affiliation_context() -> None:
    providers = ProviderSet(github=MockGitHubProvider())
    agent = MembershipAgentV2()

    result = asyncio.run(agent.run(_membership_context(), providers))
    memberships = result.stats["memberships"]
    epfl_membership = next(
        membership
        for membership in memberships
        if membership["org:organization"] == "https://ror.org/02s376052"
    )

    assert epfl_membership["org:role"] == "Research Engineer"
    assert epfl_membership["time:hasBeginning"] == "2021-01-01"
    assert epfl_membership["time:hasEnd"] is None


def test_membership_agent_handles_unresolved_organizations_with_warnings() -> None:
    providers = ProviderSet(github=MockGitHubProvider())
    agent = MembershipAgentV2()
    context = deepcopy(_membership_context())
    context["known_persons"][0]["affiliations"] = ["Missing Organization"]
    context["known_persons"][0]["orcid_affiliations"] = []

    result = asyncio.run(agent.run(context, providers))

    assert result.data == {}
    assert result.stats["memberships"] == []
    assert any("Unresolved membership organization mapping" in warning for warning in result.warnings)
