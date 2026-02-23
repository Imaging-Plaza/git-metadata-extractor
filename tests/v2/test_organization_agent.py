from __future__ import annotations

import asyncio
from typing import Any, Callable

from jsonschema import validate

from src.v2.agents import OrganizationAgentV2, ProviderSet
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_ror import MockRORProvider


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
    assert result.data["id"] == "github"


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
