from __future__ import annotations

from copy import deepcopy
from typing import Any

from git_metadata_extractor.agents import LLMCriticAgentV2, ProviderSet
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.pipeline.stages.models import LLMCriticStageResult, ReconciledEntities

BUCKET_TO_SINGULAR = {
    "organizations": "organization",
    "persons": "person",
    "repositories": "repository",
    "articles": "article",
}


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def _collect_repository_owner_org_ids(repositories: list[dict[str, Any]]) -> set[str]:
    owner_org_ids: set[str] = set()
    for repository in repositories:
        owner_id = repository.get("pulse:ownedBy")
        if isinstance(owner_id, str) and owner_id.strip():
            owner_org_ids.add(owner_id.strip())
    return owner_org_ids


def _collect_repository_ids(repositories: list[dict[str, Any]]) -> set[str]:
    repository_ids: set[str] = set()
    for repository in repositories:
        repository_id = repository.get("id")
        if isinstance(repository_id, str) and repository_id.strip():
            repository_ids.add(repository_id.strip())
    return repository_ids


def _collect_contributor_person_ids(
    contributions: list[dict[str, Any]],
    *,
    repository_ids: set[str],
    dropped_person_ids: set[str],
) -> set[str]:
    person_ids: set[str] = set()
    for contribution in contributions:
        contribution_repo_id = contribution.get("pulse:contributionTo")
        if not isinstance(contribution_repo_id, str) or contribution_repo_id not in repository_ids:
            continue
        person_id = contribution.get("schema:author")
        if not isinstance(person_id, str) or not person_id.strip():
            continue
        if person_id in dropped_person_ids:
            continue
        person_ids.add(person_id.strip())
    return person_ids


def _collect_membership_org_ids_for_persons(
    memberships: list[dict[str, Any]],
    *,
    person_ids: set[str],
) -> set[str]:
    organization_ids: set[str] = set()
    for membership in memberships:
        person_id = membership.get("_person_ref")
        if not isinstance(person_id, str) or person_id not in person_ids:
            continue
        organization_id = membership.get("org:organization")
        if not isinstance(organization_id, str) or not organization_id.strip():
            continue
        organization_ids.add(organization_id.strip())
    return organization_ids


def _collect_owner_org_ancestor_ids(
    organizations: list[dict[str, Any]],
    *,
    seed_owner_ids: set[str],
) -> set[str]:
    by_id: dict[str, dict[str, Any]] = {}
    for organization in organizations:
        org_id = organization.get("id")
        if not isinstance(org_id, str) or not org_id.strip():
            continue
        by_id[org_id] = organization

    protected: set[str] = set()
    queue = [org_id for org_id in seed_owner_ids if org_id in by_id]
    while queue:
        current = queue.pop(0)
        if current in protected:
            continue
        protected.add(current)
        parents = by_id.get(current, {}).get("org:unitOf") or []
        if isinstance(parents, str):
            parents = [parents]
        if isinstance(parents, list):
            for parent_id in parents:
                if (
                    isinstance(parent_id, str)
                    and parent_id in by_id
                    and parent_id not in protected
                ):
                    queue.append(parent_id)

    return protected


def _normalize_drop_suggestions(raw_payload: Any) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    if not isinstance(raw_payload, list):
        return suggestions

    for item in raw_payload:
        if isinstance(item, str):
            candidate_id = item.strip()
            if candidate_id:
                suggestions.append({"id": candidate_id, "reason": None, "confidence": None})
            continue

        if not isinstance(item, dict):
            continue

        candidate_id = item.get("id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            continue

        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = None

        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)):
            confidence = max(0.0, min(float(confidence), 1.0))
        else:
            confidence = None

        suggestions.append(
            {
                "id": candidate_id.strip(),
                "reason": reason,
                "confidence": confidence,
            },
        )

    return suggestions


def _build_pruned_excluded_entity(
    *,
    entity_type: str,
    entity_payload: dict[str, Any],
    reason: str | None,
) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "entity": deepcopy(entity_payload),
        "reason": [
            {
                "path": "<root>",
                "message": "critic_pruned",
                "constraint": "critic_prune",
                "expected": reason,
            },
        ],
    }


def _drop_primary_entities(
    *,
    entities_by_bucket: dict[str, list[dict[str, Any]]],
    drop_ids_by_bucket: dict[str, set[str]],
    reason_by_id_by_bucket: dict[str, dict[str, str | None]],
) -> list[dict[str, Any]]:
    pruned_excluded_entities: list[dict[str, Any]] = []

    for bucket, entities in entities_by_bucket.items():
        singular = BUCKET_TO_SINGULAR[bucket]
        kept: list[dict[str, Any]] = []
        drop_ids = drop_ids_by_bucket.get(bucket, set())
        reason_by_id = reason_by_id_by_bucket.get(bucket, {})

        for entity in entities:
            entity_id = entity.get("id")
            if not isinstance(entity_id, str) or entity_id not in drop_ids:
                kept.append(entity)
                continue

            pruned_excluded_entities.append(
                _build_pruned_excluded_entity(
                    entity_type=singular,
                    entity_payload=entity,
                    reason=reason_by_id.get(entity_id),
                ),
            )

        entities_by_bucket[bucket] = kept

    return pruned_excluded_entities


def _cleanup_relationships_after_prune(
    *,
    entities_by_bucket: dict[str, list[dict[str, Any]]],
    memberships: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    dropped_person_ids: set[str],
    dropped_org_ids: set[str],
    dropped_repo_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    repositories = entities_by_bucket["repositories"]
    articles = entities_by_bucket["articles"]
    persons = entities_by_bucket["persons"]
    organizations = entities_by_bucket["organizations"]

    for repository in repositories:
        authors = repository.get("schema:author")
        if isinstance(authors, list):
            repository["schema:author"] = _dedupe_strings(
                [
                    author
                    for author in authors
                    if isinstance(author, str) and author and author not in dropped_person_ids
                ],
            )

        owner = repository.get("pulse:ownedBy")
        if isinstance(owner, str) and (owner in dropped_person_ids or owner in dropped_org_ids):
            repository["pulse:ownedBy"] = None

        fork_of = repository.get("pulse:isForkOf")
        if isinstance(fork_of, str) and fork_of in dropped_repo_ids:
            repository["pulse:isForkOf"] = None

    for article in articles:
        authors = article.get("schema:author")
        if isinstance(authors, list):
            article["schema:author"] = _dedupe_strings(
                [
                    author
                    for author in authors
                    if isinstance(author, str) and author and author not in dropped_person_ids
                ],
            )

        source_org = article.get("schema:sourceOrganization")
        if isinstance(source_org, str) and source_org in dropped_org_ids:
            article["schema:sourceOrganization"] = None

    for person in persons:
        affiliations = person.get("affiliations")
        if isinstance(affiliations, list):
            cleaned_affiliations: list[Any] = []
            for affiliation in affiliations:
                if isinstance(affiliation, str):
                    if affiliation in dropped_org_ids:
                        continue
                    cleaned_affiliations.append(affiliation)
                    continue

                if isinstance(affiliation, dict):
                    updated = deepcopy(affiliation)
                    org_id = updated.get("organizationId")
                    if isinstance(org_id, str) and org_id in dropped_org_ids:
                        continue
                    cleaned_affiliations.append(updated)
                    continue

                cleaned_affiliations.append(affiliation)

            person["affiliations"] = cleaned_affiliations

        owns = person.get("pulse:owns")
        if isinstance(owns, list):
            person["pulse:owns"] = _dedupe_strings(
                [
                    repo_id
                    for repo_id in owns
                    if isinstance(repo_id, str) and repo_id and repo_id not in dropped_repo_ids
                ],
            )

    for organization in organizations:
        has_units = organization.get("org:hasUnit")
        if isinstance(has_units, list):
            organization["org:hasUnit"] = _dedupe_strings(
                [
                    unit_id
                    for unit_id in has_units
                    if isinstance(unit_id, str) and unit_id and unit_id not in dropped_org_ids
                ],
            )

        unit_of = organization.get("org:unitOf")
        if isinstance(unit_of, str):
            organization["org:unitOf"] = (
                [] if unit_of in dropped_org_ids else [unit_of]
            )
        elif isinstance(unit_of, list):
            organization["org:unitOf"] = [
                parent_id
                for parent_id in unit_of
                if isinstance(parent_id, str) and parent_id and parent_id not in dropped_org_ids
            ]

        owns = organization.get("pulse:owns")
        if isinstance(owns, list):
            organization["pulse:owns"] = _dedupe_strings(
                [
                    repo_id
                    for repo_id in owns
                    if isinstance(repo_id, str) and repo_id and repo_id not in dropped_repo_ids
                ],
            )

    surviving_memberships: list[dict[str, Any]] = []
    removed_memberships = 0
    for membership in memberships:
        person_id = membership.get("_person_ref")
        if not isinstance(person_id, str):
            person_id = None
            membership_id = membership.get("id")
            if isinstance(membership_id, str) and "_" in membership_id:
                person_id = membership_id.split("_", maxsplit=1)[0]

        organization_id = membership.get("org:organization")
        if not isinstance(organization_id, str):
            organization_id = None
            membership_id = membership.get("id")
            if isinstance(membership_id, str) and "_" in membership_id:
                organization_id = membership_id.split("_", maxsplit=1)[1]

        if (
            isinstance(person_id, str)
            and person_id in dropped_person_ids
            or isinstance(organization_id, str)
            and organization_id in dropped_org_ids
        ):
            removed_memberships += 1
            continue

        surviving_memberships.append(membership)

    surviving_contributions: list[dict[str, Any]] = []
    removed_contributions = 0
    for contribution in contributions:
        person_id = contribution.get("schema:author")
        if not isinstance(person_id, str):
            person_id = None
            contribution_id = contribution.get("id")
            if isinstance(contribution_id, str) and "_" in contribution_id:
                person_id = contribution_id.split("_", maxsplit=1)[0]

        repository_id = contribution.get("pulse:contributionTo")
        if not isinstance(repository_id, str):
            repository_id = None
            contribution_id = contribution.get("id")
            if isinstance(contribution_id, str) and "_" in contribution_id:
                repository_id = contribution_id.split("_", maxsplit=1)[1]

        if (
            isinstance(person_id, str)
            and person_id in dropped_person_ids
            or isinstance(repository_id, str)
            and repository_id in dropped_repo_ids
        ):
            removed_contributions += 1
            continue

        surviving_contributions.append(contribution)

    memberships_by_person: dict[str, list[str]] = {}
    for membership in surviving_memberships:
        person_id = membership.get("_person_ref")
        membership_id = membership.get("id")
        if not isinstance(person_id, str) or not isinstance(membership_id, str):
            continue
        memberships_by_person.setdefault(person_id, []).append(membership_id)

    contributions_by_person: dict[str, list[str]] = {}
    for contribution in surviving_contributions:
        person_id = contribution.get("schema:author")
        contribution_id = contribution.get("id")
        if not isinstance(person_id, str) or not isinstance(contribution_id, str):
            continue
        contributions_by_person.setdefault(person_id, []).append(contribution_id)

    for person in persons:
        person_id = person.get("id")
        if not isinstance(person_id, str):
            continue
        person["org:hasMembership"] = _dedupe_strings(memberships_by_person.get(person_id, []))
        person["pulse:hasContribution"] = _dedupe_strings(contributions_by_person.get(person_id, []))

    return (
        surviving_memberships,
        surviving_contributions,
        removed_memberships,
        removed_contributions,
    )


async def run_llm_critic_stage(  # noqa: PLR0913
    *,
    reconciled: ReconciledEntities,
    source_url: str,
    detected_type: str,
    providers: ProviderSet,
    initial_context: dict[str, Any] | None = None,
    pipeline_outputs: dict[str, Any] | None = None,
    max_concurrency: int = 3,
    llm_call_timeout_seconds: float = 180.0,
    agent: LLMCriticAgentV2 | None = None,
    cache: ProviderCache | None = None,
) -> LLMCriticStageResult:
    del max_concurrency
    entities_by_bucket = {
        "persons": deepcopy(reconciled.entities.get("persons", [])),
        "organizations": deepcopy(reconciled.entities.get("organizations", [])),
        "repositories": deepcopy(reconciled.entities.get("repositories", [])),
        "articles": deepcopy(reconciled.entities.get("articles", [])),
    }
    memberships = deepcopy(reconciled.memberships)
    contributions = deepcopy(reconciled.contributions)

    critic_agent = agent or LLMCriticAgentV2(
        llm_call_timeout_seconds=llm_call_timeout_seconds,
        cache=cache,
    )
    agent_result = await critic_agent.run(
        {
            "source_url": source_url,
            "detected_type": detected_type,
            "initial_context": deepcopy(initial_context) if isinstance(initial_context, dict) else {},
            "pipeline_outputs": deepcopy(pipeline_outputs) if isinstance(pipeline_outputs, dict) else {},
            "reconciled_entities": deepcopy(entities_by_bucket),
            "memberships": deepcopy(memberships),
            "contributions": deepcopy(contributions),
        },
        providers,
    )

    decisions = {
        "organizations": _normalize_drop_suggestions(agent_result.data.get("organizations")),
        "persons": _normalize_drop_suggestions(agent_result.data.get("persons")),
        "repositories": _normalize_drop_suggestions(agent_result.data.get("repositories")),
        "articles": _normalize_drop_suggestions(agent_result.data.get("articles")),
    }

    existing_ids_by_bucket = {
        bucket: {
            entity_id
            for entity_id in (
                entity.get("id") if isinstance(entity, dict) else None
                for entity in entities_by_bucket[bucket]
            )
            if isinstance(entity_id, str) and entity_id
        }
        for bucket in BUCKET_TO_SINGULAR
    }

    drop_ids_by_bucket: dict[str, set[str]] = {
        bucket: set()
        for bucket in BUCKET_TO_SINGULAR
    }
    reason_by_id_by_bucket: dict[str, dict[str, str | None]] = {
        bucket: {}
        for bucket in BUCKET_TO_SINGULAR
    }

    for bucket, suggestions in decisions.items():
        for suggestion in suggestions:
            candidate_id = suggestion["id"]
            if candidate_id not in existing_ids_by_bucket[bucket]:
                continue
            drop_ids_by_bucket[bucket].add(candidate_id)
            reason_by_id_by_bucket[bucket][candidate_id] = suggestion.get("reason")

    owner_org_ids = _collect_repository_owner_org_ids(entities_by_bucket["repositories"])
    protected_owner_context_org_ids = _collect_owner_org_ancestor_ids(
        entities_by_bucket["organizations"],
        seed_owner_ids=owner_org_ids,
    )
    for protected_org_id in protected_owner_context_org_ids:
        drop_ids_by_bucket["organizations"].discard(protected_org_id)
        reason_by_id_by_bucket["organizations"].pop(protected_org_id, None)

    root_bucket = {
        "repository": "repositories",
        "user": "persons",
        "organization": "organizations",
    }.get(detected_type)
    root_protected: list[str] = []
    if isinstance(root_bucket, str) and entities_by_bucket[root_bucket]:
        root_id = entities_by_bucket[root_bucket][0].get("id")
        if isinstance(root_id, str) and root_id in drop_ids_by_bucket[root_bucket]:
            drop_ids_by_bucket[root_bucket].remove(root_id)
            reason_by_id_by_bucket[root_bucket].pop(root_id, None)
            root_protected.append(root_id)

    protected_contributor_affiliation_org_ids: set[str] = set()
    repository_ids = _collect_repository_ids(entities_by_bucket["repositories"])
    if repository_ids:
        contributing_person_ids = _collect_contributor_person_ids(
            contributions,
            repository_ids=repository_ids,
            dropped_person_ids=drop_ids_by_bucket["persons"],
        )
        protected_contributor_affiliation_org_ids = _collect_membership_org_ids_for_persons(
            memberships,
            person_ids=contributing_person_ids,
        )
        for protected_org_id in protected_contributor_affiliation_org_ids:
            drop_ids_by_bucket["organizations"].discard(protected_org_id)
            reason_by_id_by_bucket["organizations"].pop(protected_org_id, None)

    pruned_excluded_entities = _drop_primary_entities(
        entities_by_bucket=entities_by_bucket,
        drop_ids_by_bucket=drop_ids_by_bucket,
        reason_by_id_by_bucket=reason_by_id_by_bucket,
    )

    dropped_person_ids = drop_ids_by_bucket["persons"]
    dropped_org_ids = drop_ids_by_bucket["organizations"]
    dropped_repo_ids = drop_ids_by_bucket["repositories"]
    (
        surviving_memberships,
        surviving_contributions,
        removed_memberships,
        removed_contributions,
    ) = _cleanup_relationships_after_prune(
        entities_by_bucket=entities_by_bucket,
        memberships=memberships,
        contributions=contributions,
        dropped_person_ids=dropped_person_ids,
        dropped_org_ids=dropped_org_ids,
        dropped_repo_ids=dropped_repo_ids,
    )

    proposed_drop_count = sum(len(suggestions) for suggestions in decisions.values())
    applied_drop_count = len(pruned_excluded_entities)
    dropped_by_type = {
        bucket: len(drop_ids)
        for bucket, drop_ids in drop_ids_by_bucket.items()
    }

    warnings: list[str] = [
        (
            "LLM critic summary: "
            f"proposed={proposed_drop_count}, "
            f"applied={applied_drop_count}, "
            f"root_protected={len(root_protected)}, "
            f"cascade_memberships_removed={removed_memberships}, "
            f"cascade_contributions_removed={removed_contributions}"
        ),
    ]
    if root_protected:
        warnings.append(
            "LLM critic root protection override: "
            + ", ".join(root_protected),
        )
    if protected_owner_context_org_ids:
        warnings.append(
            "LLM critic owner-context org protection override: "
            + ", ".join(sorted(protected_owner_context_org_ids)),
        )
    if protected_contributor_affiliation_org_ids:
        warnings.append(
            "LLM critic contributor-affiliation org protection override: "
            + ", ".join(sorted(protected_contributor_affiliation_org_ids)),
        )

    reconciled_output = ReconciledEntities(
        entities=entities_by_bucket,
        memberships=surviving_memberships,
        contributions=surviving_contributions,
        link_warnings=list(reconciled.link_warnings),
        reconciliation_debug=deepcopy(reconciled.reconciliation_debug),
    )

    applied_payload = {
        "proposed_drop_count": proposed_drop_count,
        "applied_drop_count": applied_drop_count,
        "dropped_by_type": dropped_by_type,
        "protected_root_ids": root_protected,
        "protected_owner_context_org_ids": sorted(protected_owner_context_org_ids),
        "protected_contributor_affiliation_org_ids": sorted(
            protected_contributor_affiliation_org_ids,
        ),
        "cascade_memberships_removed": removed_memberships,
        "cascade_contributions_removed": removed_contributions,
    }

    return LLMCriticStageResult(
        reconciled=reconciled_output,
        warnings=warnings,
        decisions=decisions,
        applied=applied_payload,
        pruned_excluded_entities=pruned_excluded_entities,
    )
