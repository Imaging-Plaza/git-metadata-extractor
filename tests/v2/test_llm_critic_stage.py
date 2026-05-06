from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents import ProviderSet
from src.v2.agents.models import AgentResult
from src.v2.pipeline.stages.llm_critic import run_llm_critic_stage
from src.v2.pipeline.stages.models import ReconciledEntities
from src.v2.ingest.providers.mock_github import MockGitHubProvider


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


class _StaticCriticAgent:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del context, providers
        return AgentResult(data=self._payload)


class _CapturingCriticAgent:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.captured_context: dict[str, Any] | None = None

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del providers
        self.captured_context = context
        return AgentResult(data=self._payload)


def test_llm_critic_stage_receives_initial_context_and_pipeline_outputs() -> None:
    agent = _CapturingCriticAgent(
        {
            "organizations": [],
            "persons": [],
            "repositories": [],
            "articles": [],
        },
    )
    reconciled = ReconciledEntities(
        entities={
            "repositories": [{"id": "repo-1", "type": "schema:SoftwareSourceCode"}],
            "persons": [],
            "organizations": [],
            "articles": [],
        },
        memberships=[],
        contributions=[],
    )

    asyncio.run(
        run_llm_critic_stage(
            reconciled=reconciled,
            source_url="https://github.com/owner/repo",
            detected_type="repository",
            providers=_providers(),
            initial_context={"repository": {"readme_content": "README"}},
            pipeline_outputs={"repo_agent": {"id": "repo-1"}},
            agent=agent,
        ),
    )

    assert isinstance(agent.captured_context, dict)
    assert agent.captured_context["source_url"] == "https://github.com/owner/repo"
    assert agent.captured_context["detected_type"] == "repository"
    assert agent.captured_context["initial_context"]["repository"]["readme_content"] == "README"
    assert agent.captured_context["pipeline_outputs"]["repo_agent"]["id"] == "repo-1"


def test_llm_critic_root_protection_and_cascade_cleanup() -> None:
    repo_id = "https://github.com/owner/repo"
    person_keep = "https://orcid.org/0000-0000-0000-0001"
    person_drop = "https://orcid.org/0000-0000-0000-0002"
    org_keep = "https://ror.org/02hdt9m26"
    org_drop = "https://ror.org/01swzsf04"

    reconciled = ReconciledEntities(
        entities={
            "repositories": [
                {
                    "id": repo_id,
                    "type": "schema:SoftwareSourceCode",
                    "schema:author": [person_keep, person_drop],
                    "pulse:ownedBy": org_keep,
                },
            ],
            "persons": [
                {
                    "id": person_keep,
                    "type": "schema:Person",
                    "org:hasMembership": [],
                    "pulse:hasContribution": [],
                    "pulse:owns": [repo_id],
                },
                {
                    "id": person_drop,
                    "type": "schema:Person",
                    "org:hasMembership": [f"{person_drop}_{org_drop}"],
                    "pulse:hasContribution": [f"{person_drop}_{repo_id}"],
                    "pulse:owns": [repo_id],
                },
            ],
            "organizations": [
                {
                    "id": org_keep,
                    "type": "org:Organization",
                    "org:hasUnit": [org_drop],
                    "org:unitOf": None,
                    "pulse:owns": [repo_id],
                },
                {
                    "id": org_drop,
                    "type": "org:Organization",
                    "org:hasUnit": [],
                    "org:unitOf": org_keep,
                    "pulse:owns": [],
                },
            ],
            "articles": [
                {
                    "id": "https://doi.org/10.1234/drop-me",
                    "type": "schema:ScholarlyArticle",
                    "schema:author": [person_drop],
                    "schema:sourceOrganization": org_drop,
                },
            ],
        },
        memberships=[
            {
                "id": f"{person_drop}_{org_drop}",
                "type": "org:Membership",
                "_person_ref": person_drop,
                "org:organization": org_drop,
            },
        ],
        contributions=[
            {
                "id": f"{person_drop}_{repo_id}",
                "type": "pulse:Contribution",
                "schema:author": person_drop,
                "pulse:contributionTo": repo_id,
            },
        ],
    )

    result = asyncio.run(
        run_llm_critic_stage(
            reconciled=reconciled,
            source_url="https://github.com/owner/repo",
            detected_type="repository",
            providers=_providers(),
            agent=_StaticCriticAgent(
                {
                    "organizations": [{"id": org_drop, "reason": "not relevant"}],
                    "persons": [{"id": person_drop, "reason": "not relevant"}],
                    "repositories": [{"id": repo_id, "reason": "should not drop root"}],
                    "articles": [{"id": "https://doi.org/10.1234/drop-me", "reason": "off-topic"}],
                },
            ),
        ),
    )

    assert repo_id in result.applied["protected_root_ids"]
    assert result.applied["applied_drop_count"] == 3

    repositories = result.reconciled.entities["repositories"]
    assert len(repositories) == 1
    assert repositories[0]["id"] == repo_id
    assert repositories[0]["schema:author"] == [person_keep]

    persons = result.reconciled.entities["persons"]
    assert len(persons) == 1
    assert persons[0]["id"] == person_keep

    organizations = result.reconciled.entities["organizations"]
    assert len(organizations) == 1
    assert organizations[0]["id"] == org_keep
    assert organizations[0]["org:hasUnit"] == []

    assert result.reconciled.entities["articles"] == []
    assert result.reconciled.memberships == []
    assert result.reconciled.contributions == []

    assert len(result.pruned_excluded_entities) == 3
    assert all(
        entry["reason"][0]["message"] == "critic_pruned"
        for entry in result.pruned_excluded_entities
    )


def test_llm_critic_protects_owner_org_ancestors_from_prune() -> None:
    repo_id = "https://github.com/sdsc-ordes/gimie"
    owner_org_id = "https://ror.org/02hdt9m26"
    host_org_id = "https://ror.org/02s376052"

    reconciled = ReconciledEntities(
        entities={
            "repositories": [
                {
                    "id": repo_id,
                    "type": "schema:SoftwareSourceCode",
                    "pulse:ownedBy": owner_org_id,
                },
            ],
            "persons": [],
            "organizations": [
                {
                    "id": owner_org_id,
                    "type": "org:Organization",
                    "org:unitOf": host_org_id,
                },
                {
                    "id": host_org_id,
                    "type": "org:Organization",
                    "org:unitOf": None,
                },
            ],
            "articles": [],
        },
        memberships=[],
        contributions=[],
    )

    result = asyncio.run(
        run_llm_critic_stage(
            reconciled=reconciled,
            source_url=repo_id,
            detected_type="repository",
            providers=_providers(),
            agent=_StaticCriticAgent(
                {
                    "organizations": [
                        {
                            "id": host_org_id,
                            "reason": (
                                "Affiliated institution of a contributor, "
                                "unrelated to repository ownership"
                            ),
                        },
                    ],
                    "persons": [],
                    "repositories": [],
                    "articles": [],
                },
            ),
        ),
    )

    organizations = result.reconciled.entities["organizations"]
    assert {organization["id"] for organization in organizations} == {owner_org_id, host_org_id}
    assert result.applied["applied_drop_count"] == 0
    assert host_org_id in result.applied["protected_owner_context_org_ids"]
    assert any(
        "owner-context org protection override" in warning
        for warning in result.warnings
    )


def test_llm_critic_protects_contributor_affiliation_orgs_for_kept_contributors() -> None:
    repo_id = "https://github.com/sdsc-ordes/gimie"
    person_id = "https://orcid.org/0000-0002-2961-2655"
    org_id = "https://ror.org/02s376052"

    reconciled = ReconciledEntities(
        entities={
            "repositories": [
                {
                    "id": repo_id,
                    "type": "schema:SoftwareSourceCode",
                },
            ],
            "persons": [
                {
                    "id": person_id,
                    "type": "schema:Person",
                },
            ],
            "organizations": [
                {
                    "id": org_id,
                    "type": "org:Organization",
                },
            ],
            "articles": [],
        },
        memberships=[
            {
                "id": f"{person_id}_{org_id}",
                "type": "org:Membership",
                "_person_ref": person_id,
                "org:organization": org_id,
            },
        ],
        contributions=[
            {
                "id": f"{person_id}_{repo_id}",
                "type": "pulse:Contribution",
                "schema:author": person_id,
                "pulse:contributionTo": repo_id,
            },
        ],
    )

    result = asyncio.run(
        run_llm_critic_stage(
            reconciled=reconciled,
            source_url=repo_id,
            detected_type="repository",
            providers=_providers(),
            agent=_StaticCriticAgent(
                {
                    "organizations": [
                        {
                            "id": org_id,
                            "reason": (
                                "University affiliation only via member profiles, "
                                "not directly linked to repository ownership or contribution"
                            ),
                        },
                    ],
                    "persons": [],
                    "repositories": [],
                    "articles": [],
                },
            ),
        ),
    )

    organizations = result.reconciled.entities["organizations"]
    assert len(organizations) == 1
    assert organizations[0]["id"] == org_id
    assert result.applied["applied_drop_count"] == 0
    assert org_id in result.applied["protected_contributor_affiliation_org_ids"]
    assert any(
        "contributor-affiliation org protection override" in warning
        for warning in result.warnings
    )
