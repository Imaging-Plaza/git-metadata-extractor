from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents import ProviderSet
from src.v2.agents.models import AgentResult
from src.v2.pipeline.stages.llm_dedup import run_llm_dedup_stage
from src.v2.providers.mock_github import MockGitHubProvider


def _providers() -> ProviderSet:
    return ProviderSet(github=MockGitHubProvider())


class _StaticDedupAgent:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del context, providers
        return AgentResult(data=self._payload)


class _CapturingDedupAgent:
    def __init__(self) -> None:
        self.captured_context: dict[str, Any] | None = None

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del providers
        self.captured_context = context
        return AgentResult(
            data={
                "organizations": [],
                "persons": [],
                "repositories": [],
                "articles": [],
            },
        )


def test_llm_dedup_stage_passes_serialized_buckets_to_agent_context() -> None:
    agent = _CapturingDedupAgent()
    buckets = {
        "repositories": [{"id": "repo-1", "type": "schema:SoftwareSourceCode"}],
        "persons": [{"id": "person-1", "type": "schema:Person"}],
        "organizations": [{"id": "org-1", "type": "org:Organization"}],
        "articles": [{"id": "article-1", "type": "schema:ScholarlyArticle"}],
        "memberships": [],
        "contributions": [],
    }

    asyncio.run(
        run_llm_dedup_stage(
            typed_entity_buckets=buckets,
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
    assert set(agent.captured_context["typed_entity_buckets"]) == {
        "repositories",
        "persons",
        "organizations",
        "articles",
        "memberships",
        "contributions",
    }


def test_llm_dedup_merges_person_cluster_and_propagates_composite_ids() -> None:
    canonical_person_id = "https://orcid.org/0000-0000-0000-0001"
    duplicate_person_id = "https://github.com/alice"
    org_id = "https://ror.org/02hdt9m26"
    repo_id = "https://github.com/owner/repo"

    typed_entity_buckets = {
        "repositories": [
            {
                "id": repo_id,
                "type": "schema:SoftwareSourceCode",
                "schema:author": [duplicate_person_id],
                "pulse:ownedBy": org_id,
                "identifiers": {
                    "pulse:githubRepositoryHandle": "owner/repo",
                    "schema:citation": None,
                    "uuid": "3adbb05f-80c1-44c6-8f8b-066eb65407ec",
                },
                "idSource": "pulse:githubRepositoryHandle",
            },
        ],
        "persons": [
            {
                "id": canonical_person_id,
                "type": "schema:Person",
                "identifiers": {
                    "pulse:orcid": "0000-0000-0000-0001",
                    "pulse:infosciencePersonIdentifier": None,
                    "pulse:githubUsername": None,
                    "uuid": "8cf4fd8d-00da-4f87-a6f8-e0bfe6f0c9d2",
                },
                "idSource": "pulse:orcid",
                "schema:name": "Alice Canonical",
            },
            {
                "id": duplicate_person_id,
                "type": "schema:Person",
                "identifiers": {
                    "pulse:orcid": "0000-0000-0000-0001",
                    "pulse:infosciencePersonIdentifier": None,
                    "pulse:githubUsername": "alice",
                    "uuid": "dd8cc8f8-8e7f-4fef-9d79-1fc297f52f70",
                },
                "idSource": "pulse:githubUsername",
                "schema:name": "alice",
                "schema:url": duplicate_person_id,
            },
        ],
        "organizations": [
            {
                "id": org_id,
                "type": "org:Organization",
                "identifiers": {
                    "pulse:ror": org_id,
                    "pulse:infoscienceOrganizationIdentifier": None,
                    "pulse:githubOrganizationHandle": "owner",
                    "uuid": "2ca0c4f8-2a62-4885-b736-c90aeb913f90",
                },
                "idSource": "pulse:ror",
                "schema:name": "Owner Org",
            },
        ],
        "articles": [],
        "memberships": [
            {
                "id": f"{duplicate_person_id}_{org_id}",
                "type": "org:Membership",
                "identifiers": {
                    "pulse:composite": f"{duplicate_person_id}_{org_id}",
                    "uuid": "61a1b652-0527-44b9-87c4-5527f8fc35a3",
                },
                "idSource": "pulse:composite",
                "_person_ref": duplicate_person_id,
                "org:organization": org_id,
            },
        ],
        "contributions": [
            {
                "id": f"{duplicate_person_id}_{repo_id}",
                "type": "pulse:Contribution",
                "identifiers": {
                    "pulse:composite": f"{duplicate_person_id}_{repo_id}",
                    "uuid": "d2e38f7d-2d8f-4fee-8855-073d7528ac6c",
                },
                "idSource": "pulse:composite",
                "schema:author": duplicate_person_id,
                "pulse:contributionTo": repo_id,
            },
        ],
    }

    result = asyncio.run(
        run_llm_dedup_stage(
            typed_entity_buckets=typed_entity_buckets,
            source_url="https://github.com/owner/repo",
            detected_type="repository",
            providers=_providers(),
            agent=_StaticDedupAgent(
                {
                    "organizations": [],
                    "persons": [{"ids": [canonical_person_id, duplicate_person_id]}],
                    "repositories": [],
                    "articles": [],
                },
            ),
        ),
    )

    assert result.accepted_cluster_count == 1
    assert result.rejected_cluster_count == 0
    assert result.remap_count >= 1

    persons = result.typed_entity_buckets["persons"]
    assert len(persons) == 1
    assert persons[0]["id"] == canonical_person_id

    repository = result.typed_entity_buckets["repositories"][0]
    assert repository["schema:author"] == [canonical_person_id]

    memberships = result.typed_entity_buckets["memberships"]
    assert len(memberships) == 1
    assert memberships[0]["id"] == f"{canonical_person_id}_{org_id}"
    assert memberships[0]["identifiers"]["pulse:composite"] == f"{canonical_person_id}_{org_id}"

    contributions = result.typed_entity_buckets["contributions"]
    assert len(contributions) == 1
    assert contributions[0]["id"] == f"{canonical_person_id}_{repo_id}"
    assert contributions[0]["identifiers"]["pulse:composite"] == f"{canonical_person_id}_{repo_id}"

    assert persons[0]["org:hasMembership"] == [f"{canonical_person_id}_{org_id}"]
    assert persons[0]["pulse:hasContribution"] == [f"{canonical_person_id}_{repo_id}"]


def test_llm_dedup_rejects_conflicting_person_orcid_cluster() -> None:
    person_a = {
        "id": "https://orcid.org/0000-0000-0000-0001",
        "type": "schema:Person",
        "identifiers": {
            "pulse:orcid": "0000-0000-0000-0001",
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": None,
            "uuid": "4254f017-a4d4-4a38-b116-c5103f3932cd",
        },
        "idSource": "pulse:orcid",
    }
    person_b = {
        "id": "https://orcid.org/0000-0000-0000-0002",
        "type": "schema:Person",
        "identifiers": {
            "pulse:orcid": "0000-0000-0000-0002",
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": None,
            "uuid": "e95c153a-5ba2-4847-b7eb-f9f5d6f23616",
        },
        "idSource": "pulse:orcid",
    }

    result = asyncio.run(
        run_llm_dedup_stage(
            typed_entity_buckets={
                "repositories": [],
                "persons": [person_a, person_b],
                "organizations": [],
                "articles": [],
                "memberships": [],
                "contributions": [],
            },
            source_url="https://github.com/owner/repo",
            detected_type="repository",
            providers=_providers(),
            agent=_StaticDedupAgent(
                {
                    "organizations": [],
                    "persons": [{"ids": [person_a["id"], person_b["id"]]}],
                    "repositories": [],
                    "articles": [],
                },
            ),
        ),
    )

    assert result.accepted_cluster_count == 0
    assert result.rejected_cluster_count == 1
    assert len(result.typed_entity_buckets["persons"]) == 2
    rejected = result.resolution["rejected_clusters"]["persons"][0]
    assert rejected["reason_code"] == "conflicting_orcid_identifiers"
