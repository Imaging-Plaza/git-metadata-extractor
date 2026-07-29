from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from git_metadata_extractor.agents import LLMDedupAgentV2, ProviderSet
from git_metadata_extractor.agents.models import generate_uuid
from git_metadata_extractor.canonicalization import (
    resolve_article_id,
    resolve_organization_id,
    resolve_person_id,
    resolve_repository_id,
)
from git_metadata_extractor.pipeline.stages.models import LLMDedupStageResult

PRIMARY_BUCKETS: tuple[str, ...] = ("organizations", "persons", "repositories", "articles")
BUCKET_KEYS: tuple[str, ...] = (
    "organizations",
    "persons",
    "repositories",
    "articles",
    "memberships",
    "contributions",
)
INFOSCIENCE_UUID_PATTERN = re.compile(
    r"(?:entities/(?:person|organization|publication)|core/items)/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})",
    flags=re.IGNORECASE,
)


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, set, tuple)):
        return len(value) > 0
    return True


def _entity_score(entity: dict[str, Any]) -> int:
    score = 0
    for key, value in entity.items():
        if key in {"id", "idSource", "type", "shacl", "identifiers"}:
            continue
        if _is_non_empty(value):
            score += 1
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        score += sum(1 for value in identifiers.values() if _is_non_empty(value))
    return score


def _lookup_identifier(entity: dict[str, Any], *keys: str) -> str | None:
    identifiers = entity.get("identifiers")
    for key in keys:
        direct = entity.get(key)
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        if isinstance(identifiers, dict):
            nested = identifiers.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def _normalize_orcid(value: str | None) -> str | None:
    """Return canonical ORCID URL for use as a dedup key. v3.0.0:
    URL form matches entity field values so dedup comparisons work
    regardless of which input shape an upstream provider produced."""
    from git_metadata_extractor.canonicalization.orcid import orcid_iri

    return orcid_iri(value)


def _normalize_ror(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.lower().startswith("https://ror.org/"):
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    candidate = candidate.lower()
    if re.fullmatch(r"[0-9a-z]{9}", candidate) is None:
        return None
    return f"https://ror.org/{candidate}"


def _normalize_infoscience_uuid(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    direct_match = re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        candidate,
        flags=re.IGNORECASE,
    )
    if direct_match:
        return candidate.lower()
    match = INFOSCIENCE_UUID_PATTERN.search(candidate)
    if match is None:
        return None
    return match.group(1).lower()


def _normalize_github_handle(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.lower().startswith("https://github.com/"):
        candidate = candidate[len("https://github.com/") :]
        candidate = candidate.split("/", maxsplit=1)[0]
    if candidate.startswith("@"):
        candidate = candidate[1:]
    return candidate.casefold() if candidate else None


def _normalize_repository_handle(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.lower().startswith("https://github.com/"):
        candidate = candidate[len("https://github.com/") :]
    parts = [part for part in candidate.split("/") if part]
    if len(parts) != 2:
        return None
    return f"{parts[0]}/{parts[1]}".casefold()


def _normalize_doi(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ):
        if candidate.lower().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    return candidate.lower() if "/" in candidate else None


def _priority_rank(bucket: str, entity: dict[str, Any]) -> int:
    if bucket == "organizations":
        if _normalize_ror(_lookup_identifier(entity, "pulse:ror", "schema:identifier")):
            return 4
        if _normalize_infoscience_uuid(
            _lookup_identifier(entity, "pulse:infoscienceOrganizationIdentifier"),
        ):
            return 3
        if _normalize_github_handle(_lookup_identifier(entity, "pulse:githubOrganizationHandle")):
            return 2
        return 1

    if bucket == "persons":
        if _normalize_orcid(_lookup_identifier(entity, "pulse:orcid", "pulse:orcidIdentifier")):
            return 4
        if _normalize_infoscience_uuid(_lookup_identifier(entity, "pulse:infosciencePersonIdentifier")):
            return 3
        if _normalize_github_handle(_lookup_identifier(entity, "pulse:githubUsername")):
            return 2
        return 1

    if bucket == "repositories":
        if _normalize_repository_handle(_lookup_identifier(entity, "pulse:githubRepositoryHandle")):
            return 3
        if _normalize_doi(_lookup_identifier(entity, "schema:citation", "schema:identifier")):
            return 2
        return 1

    if _normalize_doi(_lookup_identifier(entity, "schema:identifier")):
        return 3
    if _normalize_infoscience_uuid(_lookup_identifier(entity, "pulse:infoscienceArticleIdentifier")):
        return 2
    return 1


def _cluster_conflict_reason(bucket: str, entities: list[dict[str, Any]]) -> str | None:
    if bucket == "organizations":
        rors = {
            normalized
            for normalized in (
                _normalize_ror(_lookup_identifier(entity, "pulse:ror", "schema:identifier"))
                for entity in entities
            )
            if isinstance(normalized, str)
        }
        if len(rors) > 1:
            return "conflicting_ror_identifiers"

    if bucket == "persons":
        orcids = {
            normalized
            for normalized in (
                _normalize_orcid(_lookup_identifier(entity, "pulse:orcid", "pulse:orcidIdentifier"))
                for entity in entities
            )
            if isinstance(normalized, str)
        }
        if len(orcids) > 1:
            return "conflicting_orcid_identifiers"

    if bucket == "repositories":
        handles = {
            normalized
            for normalized in (
                _normalize_repository_handle(
                    _lookup_identifier(entity, "pulse:githubRepositoryHandle"),
                )
                for entity in entities
            )
            if isinstance(normalized, str)
        }
        if len(handles) > 1:
            return "conflicting_github_repository_handles"

    if bucket == "articles":
        dois = {
            normalized
            for normalized in (
                _normalize_doi(_lookup_identifier(entity, "schema:identifier"))
                for entity in entities
            )
            if isinstance(normalized, str)
        }
        if len(dois) > 1:
            return "conflicting_doi_identifiers"

    return None


def _select_cluster_winner(bucket: str, entities: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        entities,
        key=lambda entity: (
            -_priority_rank(bucket, entity),
            -_entity_score(entity),
            str(entity.get("id") or ""),
        ),
    )[0]


def _merge_entity_payload(canonical: dict[str, Any], candidate: dict[str, Any]) -> None:
    canonical_identifiers = canonical.get("identifiers")
    if not isinstance(canonical_identifiers, dict):
        canonical_identifiers = {}
    candidate_identifiers = candidate.get("identifiers")
    if isinstance(candidate_identifiers, dict):
        for key, value in candidate_identifiers.items():
            if not _is_non_empty(value):
                continue
            existing = canonical_identifiers.get(key)
            if isinstance(existing, list) and isinstance(value, list):
                canonical_identifiers[key] = _dedupe_strings(
                    [
                        *[item for item in existing if isinstance(item, str)],
                        *[item for item in value if isinstance(item, str)],
                    ],
                )
                continue
            if not _is_non_empty(existing):
                canonical_identifiers[key] = deepcopy(value)
    canonical["identifiers"] = canonical_identifiers

    # `_stub` marks a reference-only placeholder; a merged entity is a stub only
    # if BOTH sides were stubs — never copy a placeholder's `_stub` onto a fuller
    # same-id entity (Bug 07 merge contamination).
    candidate_stub = bool(candidate.get("_stub"))

    for key, value in candidate.items():
        if key in {"id", "idSource", "identifiers", "_stub"}:
            continue
        if not _is_non_empty(value):
            continue

        existing = canonical.get(key)
        if isinstance(existing, list) and isinstance(value, list):
            merged_list: list[Any] = []
            for item in [*existing, *value]:
                if item in merged_list:
                    continue
                merged_list.append(deepcopy(item))
            canonical[key] = merged_list
            continue

        if isinstance(existing, dict) and isinstance(value, dict):
            merged_dict = deepcopy(existing)
            for nested_key, nested_value in value.items():
                if not _is_non_empty(nested_value):
                    continue
                if not _is_non_empty(merged_dict.get(nested_key)):
                    merged_dict[nested_key] = deepcopy(nested_value)
            canonical[key] = merged_dict
            continue

        if not _is_non_empty(existing):
            canonical[key] = deepcopy(value)

    if canonical.get("_stub") and not candidate_stub:
        canonical.pop("_stub", None)


def _resolve_entity_identity(bucket: str, entity: dict[str, Any]) -> tuple[str, str]:
    normalized_entity = deepcopy(entity)
    normalized_entity.pop("id", None)
    normalized_entity.pop("idSource", None)

    if bucket == "organizations":
        return resolve_organization_id(normalized_entity)
    if bucket == "persons":
        return resolve_person_id(normalized_entity)
    if bucket == "repositories":
        return resolve_repository_id(normalized_entity)
    return resolve_article_id(normalized_entity)


def _normalize_cluster_payload(raw_payload: Any) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    if not isinstance(raw_payload, list):
        return clusters

    for item in raw_payload:
        ids: list[str] = []
        reason: str | None = None
        confidence: float | None = None

        if isinstance(item, list):
            ids = [value for value in item if isinstance(value, str) and value.strip()]
        elif isinstance(item, dict):
            raw_ids = item.get("ids")
            if not isinstance(raw_ids, list):
                raw_ids = item.get("cluster") if isinstance(item.get("cluster"), list) else []
            ids = [value for value in raw_ids if isinstance(value, str) and value.strip()]
            raw_reason = item.get("reason")
            if isinstance(raw_reason, str) and raw_reason.strip():
                reason = raw_reason.strip()
            raw_confidence = item.get("confidence")
            if isinstance(raw_confidence, (int, float)):
                bounded = max(0.0, min(float(raw_confidence), 1.0))
                confidence = bounded
        else:
            continue

        deduped_ids = _dedupe_strings(ids)
        clusters.append(
            {
                "ids": deduped_ids,
                "reason": reason,
                "confidence": confidence,
            },
        )

    return clusters


def _remap_string(value: Any, mapping: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    return mapping.get(value, value)


def _parse_composite_id(composite_id: Any) -> tuple[str | None, str | None]:
    if not isinstance(composite_id, str) or "_" not in composite_id:
        return None, None
    left, right = composite_id.split("_", maxsplit=1)
    if not left or not right:
        return None, None
    return left, right


def _resolve_membership_person_id(membership: dict[str, Any]) -> str | None:
    for key in ("_person_ref", "schema:author"):
        value = membership.get(key)
        if isinstance(value, str) and value:
            return value
    left, _ = _parse_composite_id(membership.get("id"))
    return left


def _resolve_membership_org_id(membership: dict[str, Any]) -> str | None:
    org_ref = membership.get("org:organization")
    if isinstance(org_ref, str) and org_ref:
        return org_ref
    _, right = _parse_composite_id(membership.get("id"))
    return right


def _resolve_contribution_person_id(contribution: dict[str, Any]) -> str | None:
    value = contribution.get("schema:author")
    if isinstance(value, str) and value:
        return value
    left, _ = _parse_composite_id(contribution.get("id"))
    return left


def _resolve_contribution_repo_id(contribution: dict[str, Any]) -> str | None:
    value = contribution.get("pulse:contributionTo")
    if isinstance(value, str) and value:
        return value
    _, right = _parse_composite_id(contribution.get("id"))
    return right


def _apply_remaps(typed_entity_buckets: dict[str, list[dict[str, Any]]], remaps: dict[str, dict[str, str]]) -> list[str]:
    warnings: list[str] = []
    person_map = remaps.get("persons", {})
    org_map = remaps.get("organizations", {})
    repo_map = remaps.get("repositories", {})
    all_map: dict[str, str] = {}
    for remap in remaps.values():
        all_map.update(remap)

    repositories = typed_entity_buckets["repositories"]
    for repository in repositories:
        authors = repository.get("schema:author")
        if isinstance(authors, list):
            repository["schema:author"] = _dedupe_strings(
                [
                    _remap_string(author, person_map)
                    for author in authors
                    if isinstance(author, str) and author
                ],
            )
        repository["pulse:ownedBy"] = _remap_string(repository.get("pulse:ownedBy"), all_map)
        repository["pulse:isForkOf"] = _remap_string(repository.get("pulse:isForkOf"), repo_map)

    articles = typed_entity_buckets["articles"]
    for article in articles:
        authors = article.get("schema:author")
        if isinstance(authors, list):
            article["schema:author"] = _dedupe_strings(
                [
                    _remap_string(author, person_map)
                    for author in authors
                    if isinstance(author, str) and author
                ],
            )
        article["schema:sourceOrganization"] = _remap_string(
            article.get("schema:sourceOrganization"),
            org_map,
        )

    persons = typed_entity_buckets["persons"]
    for person in persons:
        affiliations = person.get("affiliations")
        if isinstance(affiliations, list):
            remapped_affiliations: list[Any] = []
            for affiliation in affiliations:
                if isinstance(affiliation, str):
                    remapped_affiliations.append(_remap_string(affiliation, org_map))
                    continue
                if isinstance(affiliation, dict):
                    updated = deepcopy(affiliation)
                    org_id = updated.get("organizationId")
                    if isinstance(org_id, str):
                        updated["organizationId"] = _remap_string(org_id, org_map)
                    remapped_affiliations.append(updated)
                    continue
                remapped_affiliations.append(affiliation)
            person["affiliations"] = remapped_affiliations

        owns = person.get("pulse:owns")
        if isinstance(owns, list):
            person["pulse:owns"] = _dedupe_strings(
                [
                    _remap_string(repository_id, repo_map)
                    for repository_id in owns
                    if isinstance(repository_id, str) and repository_id
                ],
            )

    organizations = typed_entity_buckets["organizations"]
    for organization in organizations:
        has_units = organization.get("org:hasUnit")
        if isinstance(has_units, list):
            organization["org:hasUnit"] = _dedupe_strings(
                [
                    _remap_string(unit_id, org_map)
                    for unit_id in has_units
                    if isinstance(unit_id, str) and unit_id
                ],
            )
        unit_of_raw = organization.get("org:unitOf")
        if isinstance(unit_of_raw, str):
            unit_of_raw = [unit_of_raw]
        if isinstance(unit_of_raw, list):
            organization["org:unitOf"] = _dedupe_strings(
                [
                    _remap_string(parent_id, org_map)
                    for parent_id in unit_of_raw
                    if isinstance(parent_id, str) and parent_id
                ],
            )
        else:
            organization["org:unitOf"] = []
        owns = organization.get("pulse:owns")
        if isinstance(owns, list):
            organization["pulse:owns"] = _dedupe_strings(
                [
                    _remap_string(repository_id, repo_map)
                    for repository_id in owns
                    if isinstance(repository_id, str) and repository_id
                ],
            )

    memberships = typed_entity_buckets["memberships"]
    normalized_memberships: dict[str, dict[str, Any]] = {}
    membership_order: list[str] = []
    for membership in memberships:
        person_id = _resolve_membership_person_id(membership)
        org_id = _resolve_membership_org_id(membership)
        if isinstance(person_id, str):
            person_id = _remap_string(person_id, person_map)
        if isinstance(org_id, str):
            org_id = _remap_string(org_id, org_map)

        if not isinstance(person_id, str) or not person_id or not isinstance(org_id, str) or not org_id:
            warnings.append(
                "llm_dedup: unable to recompute membership composite id due to missing person/org reference",
            )
            continue

        membership_id = f"{person_id}_{org_id}"
        normalized = deepcopy(membership)
        identifiers = normalized.get("identifiers")
        if not isinstance(identifiers, dict):
            identifiers = {}
        identifiers["pulse:composite"] = membership_id
        if not isinstance(identifiers.get("uuid"), str) or not identifiers.get("uuid"):
            identifiers["uuid"] = generate_uuid()

        normalized["id"] = membership_id
        normalized["type"] = "org:Membership"
        normalized["shacl"] = "pulse:MembershipShape"
        normalized["identifiers"] = identifiers
        normalized["idSource"] = "pulse:composite"
        normalized["_person_ref"] = person_id
        normalized["org:organization"] = org_id

        existing = normalized_memberships.get(membership_id)
        if existing is None:
            normalized_memberships[membership_id] = normalized
            membership_order.append(membership_id)
            continue

        candidate = _select_cluster_winner(
            "articles",
            [existing, normalized],
        )
        if candidate is normalized:
            _merge_entity_payload(normalized, existing)
            normalized_memberships[membership_id] = normalized
        else:
            _merge_entity_payload(existing, normalized)

    typed_entity_buckets["memberships"] = [
        normalized_memberships[membership_id]
        for membership_id in membership_order
        if membership_id in normalized_memberships
    ]

    contributions = typed_entity_buckets["contributions"]
    normalized_contributions: dict[str, dict[str, Any]] = {}
    contribution_order: list[str] = []
    for contribution in contributions:
        person_id = _resolve_contribution_person_id(contribution)
        repository_id = _resolve_contribution_repo_id(contribution)
        if isinstance(person_id, str):
            person_id = _remap_string(person_id, person_map)
        if isinstance(repository_id, str):
            repository_id = _remap_string(repository_id, repo_map)

        if (
            not isinstance(person_id, str)
            or not person_id
            or not isinstance(repository_id, str)
            or not repository_id
        ):
            warnings.append(
                "llm_dedup: unable to recompute contribution composite id due to missing person/repository reference",
            )
            continue

        contribution_id = f"{person_id}_{repository_id}"
        normalized = deepcopy(contribution)
        identifiers = normalized.get("identifiers")
        if not isinstance(identifiers, dict):
            identifiers = {}
        identifiers["pulse:composite"] = contribution_id
        if not isinstance(identifiers.get("uuid"), str) or not identifiers.get("uuid"):
            identifiers["uuid"] = generate_uuid()

        normalized["id"] = contribution_id
        normalized["type"] = "pulse:Contribution"
        normalized["shacl"] = "pulse:ContributionShape"
        normalized["identifiers"] = identifiers
        normalized["idSource"] = "pulse:composite"
        normalized["schema:author"] = person_id
        normalized["pulse:contributionTo"] = repository_id

        existing = normalized_contributions.get(contribution_id)
        if existing is None:
            normalized_contributions[contribution_id] = normalized
            contribution_order.append(contribution_id)
            continue

        candidate = _select_cluster_winner(
            "articles",
            [existing, normalized],
        )
        if candidate is normalized:
            _merge_entity_payload(normalized, existing)
            normalized_contributions[contribution_id] = normalized
        else:
            _merge_entity_payload(existing, normalized)

    typed_entity_buckets["contributions"] = [
        normalized_contributions[contribution_id]
        for contribution_id in contribution_order
        if contribution_id in normalized_contributions
    ]

    memberships_by_person: dict[str, list[str]] = {}
    for membership in typed_entity_buckets["memberships"]:
        person_id = membership.get("_person_ref")
        membership_id = membership.get("id")
        if not isinstance(person_id, str) or not isinstance(membership_id, str):
            continue
        memberships_by_person.setdefault(person_id, []).append(membership_id)

    contributions_by_person: dict[str, list[str]] = {}
    for contribution in typed_entity_buckets["contributions"]:
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

    return warnings


def _merge_bucket_by_clusters(
    *,
    bucket: str,
    entities: list[dict[str, Any]],
    raw_clusters: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, str],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    entities_by_id: dict[str, dict[str, Any]] = {}
    for entity in entities:
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id:
            continue
        entities_by_id.setdefault(entity_id, entity)

    consumed_ids: set[str] = set()
    remap: dict[str, str] = {}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    merged_entity_by_member: dict[str, dict[str, Any]] = {}

    for cluster_payload in raw_clusters:
        ids = [value for value in cluster_payload.get("ids", []) if isinstance(value, str) and value]
        ids = _dedupe_strings(ids)
        if len(ids) <= 1:
            rejected.append({**cluster_payload, "status": "rejected", "reason_code": "singleton_or_empty"})
            continue

        unknown_ids = [entity_id for entity_id in ids if entity_id not in entities_by_id]
        if unknown_ids:
            rejected.append(
                {
                    **cluster_payload,
                    "status": "rejected",
                    "reason_code": "unknown_entity_id",
                    "unknown_ids": unknown_ids,
                },
            )
            continue

        overlapping = [entity_id for entity_id in ids if entity_id in consumed_ids]
        if overlapping:
            rejected.append(
                {
                    **cluster_payload,
                    "status": "rejected",
                    "reason_code": "overlapping_cluster",
                    "overlapping_ids": overlapping,
                },
            )
            continue

        cluster_entities = [entities_by_id[entity_id] for entity_id in ids]
        conflict_reason = _cluster_conflict_reason(bucket, cluster_entities)
        if isinstance(conflict_reason, str):
            rejected.append(
                {
                    **cluster_payload,
                    "status": "rejected",
                    "reason_code": conflict_reason,
                },
            )
            continue

        winner = _select_cluster_winner(bucket, cluster_entities)
        winner_id = winner.get("id")
        if not isinstance(winner_id, str) or not winner_id:
            rejected.append(
                {
                    **cluster_payload,
                    "status": "rejected",
                    "reason_code": "winner_missing_id",
                },
            )
            continue

        merged = deepcopy(winner)
        for entity_id in ids:
            if entity_id == winner_id:
                continue
            _merge_entity_payload(merged, entities_by_id[entity_id])

        canonical_id, canonical_source = _resolve_entity_identity(bucket, merged)
        merged["id"] = canonical_id
        merged["idSource"] = canonical_source

        for entity_id in ids:
            if entity_id != canonical_id:
                remap[entity_id] = canonical_id
            consumed_ids.add(entity_id)
            merged_entity_by_member[entity_id] = merged

        accepted.append(
            {
                **cluster_payload,
                "status": "accepted",
                "winner": winner_id,
                "canonical_id": canonical_id,
                "canonical_source": canonical_source,
            },
        )

    merged_entities: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entity in entities:
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id:
            continue

        merged_entity = merged_entity_by_member.get(entity_id)
        if isinstance(merged_entity, dict):
            candidate = deepcopy(merged_entity)
        else:
            candidate = deepcopy(entity)

        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        if candidate_id in seen_ids:
            continue
        seen_ids.add(candidate_id)
        merged_entities.append(candidate)

    return merged_entities, remap, accepted, rejected


async def run_llm_dedup_stage(  # noqa: PLR0913
    *,
    typed_entity_buckets: dict[str, list[dict[str, Any]]],
    source_url: str,
    detected_type: str,
    providers: ProviderSet,
    pipeline_outputs: dict[str, Any] | None = None,
    initial_context: dict[str, Any] | None = None,
    max_concurrency: int = 3,
    llm_call_timeout_seconds: float = 180.0,
    agent: LLMDedupAgentV2 | None = None,
) -> LLMDedupStageResult:
    del max_concurrency
    bucket_copy = {
        key: deepcopy(typed_entity_buckets.get(key, [])) if isinstance(typed_entity_buckets.get(key), list) else []
        for key in BUCKET_KEYS
    }

    dedup_agent = agent or LLMDedupAgentV2(
        llm_call_timeout_seconds=llm_call_timeout_seconds,
    )
    agent_result = await dedup_agent.run(
        {
            "source_url": source_url,
            "detected_type": detected_type,
            "typed_entity_buckets": bucket_copy,
            "pipeline_outputs": deepcopy(pipeline_outputs) if isinstance(pipeline_outputs, dict) else {},
            "initial_context": deepcopy(initial_context) if isinstance(initial_context, dict) else {},
        },
        providers,
    )

    raw_candidate_clusters = {
        bucket: _normalize_cluster_payload(
            agent_result.data.get(bucket),
        )
        for bucket in PRIMARY_BUCKETS
    }

    remaps: dict[str, dict[str, str]] = {}
    accepted_clusters_by_bucket: dict[str, list[dict[str, Any]]] = {}
    rejected_clusters_by_bucket: dict[str, list[dict[str, Any]]] = {}

    for bucket in PRIMARY_BUCKETS:
        merged_bucket, remap, accepted, rejected = _merge_bucket_by_clusters(
            bucket=bucket,
            entities=bucket_copy[bucket],
            raw_clusters=raw_candidate_clusters[bucket],
        )
        bucket_copy[bucket] = merged_bucket
        remaps[bucket] = remap
        accepted_clusters_by_bucket[bucket] = accepted
        rejected_clusters_by_bucket[bucket] = rejected

    remap_warnings = _apply_remaps(bucket_copy, remaps)

    accepted_count = sum(len(clusters) for clusters in accepted_clusters_by_bucket.values())
    rejected_count = sum(len(clusters) for clusters in rejected_clusters_by_bucket.values())
    total_remap_count = sum(len(remap) for remap in remaps.values())

    resolution_payload = {
        "accepted_clusters": accepted_clusters_by_bucket,
        "rejected_clusters": rejected_clusters_by_bucket,
        "remaps": remaps,
        "accepted_cluster_count": accepted_count,
        "rejected_cluster_count": rejected_count,
        "remap_count": total_remap_count,
    }

    return LLMDedupStageResult(
        typed_entity_buckets=bucket_copy,
        warnings=_dedupe_strings(remap_warnings),
        candidate_clusters=raw_candidate_clusters,
        resolution=resolution_payload,
        accepted_cluster_count=accepted_count,
        rejected_cluster_count=rejected_count,
        remap_count=total_remap_count,
    )
