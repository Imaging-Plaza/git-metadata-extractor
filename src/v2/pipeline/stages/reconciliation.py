from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from src.v2.canonicalization import (
    resolve_article_id,
    resolve_organization_id,
    resolve_person_id,
    resolve_repository_id,
)
from src.v2.canonicalization.string_utils import normalize_string, strip_accents
from src.v2.pipeline.stages.models import ReconciledEntities
from src.v2.pipeline.stages.privacy import anonymize_email

UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    flags=re.IGNORECASE,
)
INFOSCIENCE_UUID_PATTERN = re.compile(
    r"(?:entities/(?:person|organization|publication)|core/items)/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})",
    flags=re.IGNORECASE,
)
ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]$", flags=re.IGNORECASE)
LOOKUP_SPLIT_PATTERN = re.compile(r"\s+(?:-|–|—|\||/)\s+|;|,")
PARENTHETICAL_PATTERN = re.compile(r"\s*\([^)]*\)")


def _as_entity_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduplicated.append(value)
        seen.add(value)
    return deduplicated


def _normalize_uuid_v4(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if not candidate or not UUID_V4_PATTERN.match(candidate):
        return None
    return candidate


def _normalize_orcid_token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.lower().startswith("https://orcid.org/"):
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    if not candidate or not ORCID_PATTERN.match(candidate):
        return None
    return candidate


def _normalize_infoscience_uuid(value: Any) -> str | None:
    direct_uuid = _normalize_uuid_v4(value)
    if direct_uuid is not None:
        return direct_uuid
    if not isinstance(value, str):
        return None
    match = INFOSCIENCE_UUID_PATTERN.search(value.strip())
    if match is None:
        return None
    return _normalize_uuid_v4(match.group(1))


def _normalize_person_identifiers(person: dict[str, Any]) -> None:
    identifiers = person.get("identifiers")
    if not isinstance(identifiers, dict):
        identifiers = {}

    normalized_orcid = _normalize_orcid_token(
        identifiers.get("pulse:orcid") or person.get("pulse:orcidIdentifier"),
    )
    normalized_infoscience_id = _normalize_infoscience_uuid(
        identifiers.get("pulse:infosciencePersonIdentifier")
        or person.get("pulse:infosciencePersonIdentifier"),
    )
    github_username = identifiers.get("pulse:githubUsername")
    if not isinstance(github_username, str) or not github_username:
        github_username = person.get("pulse:githubUsername")
    if not isinstance(github_username, str) or not github_username:
        github_username = None

    uuid_value = _normalize_uuid_v4(identifiers.get("uuid"))
    if uuid_value is None:
        uuid_value = str(uuid4())

    person["identifiers"] = {
        "pulse:orcid": normalized_orcid,
        "pulse:infosciencePersonIdentifier": normalized_infoscience_id,
        "pulse:githubUsername": github_username,
        "uuid": uuid_value,
    }
    person["pulse:orcidIdentifier"] = normalized_orcid
    person["pulse:infosciencePersonIdentifier"] = normalized_infoscience_id
    person["pulse:githubUsername"] = github_username


def _normalize_organization_identifiers(organization: dict[str, Any]) -> None:
    identifiers = organization.get("identifiers")
    normalized_identifiers = (
        deepcopy(identifiers)
        if isinstance(identifiers, dict)
        else {}
    )
    normalized_infoscience_id = _normalize_infoscience_uuid(
        normalized_identifiers.get("pulse:infoscienceOrganizationIdentifier")
        or organization.get("pulse:infoscienceOrganizationIdentifier"),
    )
    normalized_identifiers["pulse:infoscienceOrganizationIdentifier"] = normalized_infoscience_id
    organization["identifiers"] = normalized_identifiers
    organization["pulse:infoscienceOrganizationIdentifier"] = normalized_infoscience_id


def _normalize_lookup_token(token: str) -> str:
    return token.strip().lower()


def _lookup_token_variants(token: str) -> list[str]:
    candidate = token.strip()
    if not candidate:
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        normalized_value = value.strip()
        if not normalized_value:
            return
        if normalized_value in seen:
            return
        seen.add(normalized_value)
        variants.append(normalized_value)

    def _add_normalized_forms(value: str) -> None:
        lowered = _normalize_lookup_token(value)
        _add(lowered)
        accent_folded = strip_accents(lowered).strip()
        _add(accent_folded)
        collapsed = normalize_string(value)
        _add(collapsed)
        _add(collapsed.replace(" ", ""))

    raw_candidates: list[str] = []
    raw_seen: set[str] = set()

    def _add_raw(value: str | None) -> None:
        if not isinstance(value, str):
            return
        normalized_value = value.strip()
        if not normalized_value or normalized_value in raw_seen:
            return
        raw_seen.add(normalized_value)
        raw_candidates.append(normalized_value)

    _add_raw(candidate)
    if candidate.startswith("@"):
        _add_raw(candidate[1:])

    for raw_value in list(raw_candidates):
        _add_raw(PARENTHETICAL_PATTERN.sub("", raw_value))

    for raw_value in list(raw_candidates):
        for segment in LOOKUP_SPLIT_PATTERN.split(raw_value):
            segment = segment.strip()
            if len(segment) >= 3:
                _add_raw(segment)

    for raw_value in raw_candidates:
        _add_normalized_forms(raw_value)

    return variants


def _register_lookup_token(lookup: dict[str, str], token: Any, canonical_id: str) -> None:
    if not isinstance(token, str):
        return
    for normalized in _lookup_token_variants(token):
        lookup[normalized] = canonical_id


def _resolve_lookup_token(lookup: dict[str, str], token: Any) -> str | None:
    if not isinstance(token, str):
        return None
    for normalized in _lookup_token_variants(token):
        resolved = lookup.get(normalized)
        if isinstance(resolved, str):
            return resolved
    return None


def _register_organization_handle_lookup_tokens(
    lookup: dict[str, str],
    handle: Any,
    canonical_id: str,
) -> None:
    if not isinstance(handle, str):
        return
    stripped_handle = handle.strip()
    if not stripped_handle:
        return

    normalized_handle = stripped_handle[1:] if stripped_handle.startswith("@") else stripped_handle
    if not normalized_handle:
        return

    _register_lookup_token(lookup, normalized_handle, canonical_id)
    _register_lookup_token(lookup, f"@{normalized_handle}", canonical_id)
    _register_lookup_token(lookup, stripped_handle, canonical_id)


def _register_person_lookup_tokens(lookup: dict[str, str], person: dict[str, Any]) -> None:
    canonical_id = person["id"]
    _register_lookup_token(lookup, canonical_id, canonical_id)
    _register_lookup_token(lookup, person.get("pulse:githubUsername"), canonical_id)
    _register_lookup_token(lookup, person.get("pulse:orcidIdentifier"), canonical_id)
    _register_lookup_token(lookup, person.get("pulse:infosciencePersonIdentifier"), canonical_id)
    _register_lookup_token(lookup, person.get("schema:name"), canonical_id)

    identifiers = person.get("identifiers")
    if isinstance(identifiers, dict):
        _register_lookup_token(lookup, identifiers.get("pulse:orcid"), canonical_id)
        _register_lookup_token(
            lookup,
            identifiers.get("pulse:infosciencePersonIdentifier"),
            canonical_id,
        )
        _register_lookup_token(lookup, identifiers.get("pulse:githubUsername"), canonical_id)
        _register_lookup_token(lookup, identifiers.get("uuid"), canonical_id)


def _register_organization_lookup_tokens(
    lookup: dict[str, str],
    organization: dict[str, Any],
) -> None:
    canonical_id = organization["id"]
    _register_lookup_token(lookup, canonical_id, canonical_id)
    _register_lookup_token(lookup, organization.get("schema:name"), canonical_id)
    _register_organization_handle_lookup_tokens(
        lookup,
        organization.get("pulse:githubOrganizationHandle"),
        canonical_id,
    )
    _register_lookup_token(lookup, organization.get("pulse:ror"), canonical_id)
    _register_lookup_token(lookup, organization.get("schema:identifier"), canonical_id)
    alternate_names = organization.get("schema:alternateName")
    if isinstance(alternate_names, list):
        for alternate_name in alternate_names:
            _register_lookup_token(lookup, alternate_name, canonical_id)
    for key in ("aliases", "acronyms"):
        values = organization.get(key)
        if isinstance(values, list):
            for value in values:
                _register_lookup_token(lookup, value, canonical_id)
    labels = organization.get("labels")
    if isinstance(labels, list):
        for label_payload in labels:
            label = label_payload
            if isinstance(label_payload, dict):
                label = label_payload.get("label")
            _register_lookup_token(lookup, label, canonical_id)

    identifiers = organization.get("identifiers")
    if isinstance(identifiers, dict):
        _register_organization_handle_lookup_tokens(
            lookup,
            identifiers.get("pulse:githubOrganizationHandle"),
            canonical_id,
        )
        _register_lookup_token(lookup, identifiers.get("pulse:ror"), canonical_id)
        _register_lookup_token(
            lookup,
            identifiers.get("pulse:infoscienceOrganizationIdentifier"),
            canonical_id,
        )


def _organization_github_handle(organization: dict[str, Any]) -> str | None:
    handle = organization.get("pulse:githubOrganizationHandle")
    if isinstance(handle, str) and handle:
        return handle
    identifiers = organization.get("identifiers")
    if isinstance(identifiers, dict):
        identifier_handle = identifiers.get("pulse:githubOrganizationHandle")
        if isinstance(identifier_handle, str) and identifier_handle:
            return identifier_handle
    return None


def _build_github_org_account_unit(
    *,
    github_handle: str,
    parent_org_id: str,
) -> dict[str, Any]:
    return {
        "id": github_handle,
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": github_handle,
            "uuid": str(uuid4()),
        },
        "idSource": "pulse:githubOrganizationHandle",
        "schema:name": github_handle,
        "schema:identifier": None,
        "pulse:githubOrganizationHandle": github_handle,
        "pulse:infoscienceOrganizationIdentifier": None,
        "pulse:OrganizationType": "pulse:OtherOrganizationType",
        "pulse:githubOrgFollowers": None,
        "org:hasUnit": [],
        "org:unitOf": parent_org_id,
        "pulse:owns": [],
    }


def _ensure_github_org_units_for_repository_owners(
    *,
    organizations: list[dict[str, Any]],
    repositories: list[dict[str, Any]],
    organization_lookup: dict[str, str],
) -> None:
    repository_owner_handles = {
        owner
        for repository in repositories
        for owner in [repository.get("pulse:ownedBy")]
        if isinstance(owner, str) and owner
    }
    if not repository_owner_handles:
        return

    organizations_by_id: dict[str, dict[str, Any]] = {
        organization["id"]: organization
        for organization in organizations
        if isinstance(organization.get("id"), str)
    }

    for organization in list(organizations):
        canonical_org_id = organization.get("id")
        if not isinstance(canonical_org_id, str) or not canonical_org_id:
            continue

        github_handle = _organization_github_handle(organization)
        if (
            not isinstance(github_handle, str)
            or not github_handle
            or github_handle == canonical_org_id
            or github_handle not in repository_owner_handles
        ):
            continue

        org_units = organization.get("org:hasUnit")
        if not isinstance(org_units, list):
            org_units = []
        organization["org:hasUnit"] = _dedupe_preserve_order(
            [
                *[value for value in org_units if isinstance(value, str) and value],
                github_handle,
            ],
        )

        github_unit = organizations_by_id.get(github_handle)
        if github_unit is None:
            github_unit = _build_github_org_account_unit(
                github_handle=github_handle,
                parent_org_id=canonical_org_id,
            )
            organizations.append(github_unit)
            organizations_by_id[github_handle] = github_unit
            _register_organization_lookup_tokens(organization_lookup, github_unit)

        github_unit["org:unitOf"] = canonical_org_id
        github_unit["type"] = "org:Organization"
        github_unit["shacl"] = "pulse:OrganizationShape"
        github_unit["pulse:githubOrganizationHandle"] = github_handle

        github_identifiers = github_unit.get("identifiers")
        if not isinstance(github_identifiers, dict):
            github_identifiers = {}
        github_identifiers["pulse:githubOrganizationHandle"] = github_handle
        if not isinstance(github_identifiers.get("uuid"), str) or not github_identifiers.get("uuid"):
            github_identifiers["uuid"] = str(uuid4())
        github_unit["identifiers"] = github_identifiers


def _prune_unresolved_organization_hierarchy_links(
    *,
    organizations: list[dict[str, Any]],
    organization_lookup: dict[str, str],
) -> list[str]:
    organization_ids = {
        organization["id"]
        for organization in organizations
        if isinstance(organization.get("id"), str)
    }

    dropped_has_unit = 0
    dropped_unit_of = 0

    for organization in organizations:
        organization_id = organization.get("id")
        if not isinstance(organization_id, str):
            continue

        has_unit_refs = organization.get("org:hasUnit")
        canonical_has_units: list[str] = []
        if isinstance(has_unit_refs, list):
            for has_unit_ref in has_unit_refs:
                if not isinstance(has_unit_ref, str) or not has_unit_ref:
                    continue
                canonical_has_unit = _resolve_lookup_token(organization_lookup, has_unit_ref)
                if (
                    canonical_has_unit is None
                    or canonical_has_unit not in organization_ids
                ):
                    dropped_has_unit += 1
                    continue
                canonical_has_units.append(canonical_has_unit)
        organization["org:hasUnit"] = _dedupe_preserve_order(canonical_has_units)

        unit_of_ref = organization.get("org:unitOf")
        if not isinstance(unit_of_ref, str) or not unit_of_ref:
            organization["org:unitOf"] = None
            continue

        canonical_unit_of = _resolve_lookup_token(organization_lookup, unit_of_ref)
        if canonical_unit_of is None or canonical_unit_of not in organization_ids:
            organization["org:unitOf"] = None
            dropped_unit_of += 1
            continue
        organization["org:unitOf"] = canonical_unit_of

    warnings: list[str] = []
    if dropped_has_unit > 0 or dropped_unit_of > 0:
        warnings.append(
            (
                "Dropped unresolved organization hierarchy references during reconciliation: "
                f"org:hasUnit={dropped_has_unit}, org:unitOf={dropped_unit_of}"
            ),
        )
    return warnings


def _register_repository_lookup_tokens(
    lookup: dict[str, str],
    repository: dict[str, Any],
) -> None:
    canonical_id = repository["id"]
    _register_lookup_token(lookup, canonical_id, canonical_id)
    _register_lookup_token(lookup, repository.get("pulse:githubRepositoryHandle"), canonical_id)
    _register_lookup_token(lookup, repository.get("schema:citation"), canonical_id)

    identifiers = repository.get("identifiers")
    if isinstance(identifiers, dict):
        _register_lookup_token(lookup, identifiers.get("pulse:githubRepositoryHandle"), canonical_id)
        _register_lookup_token(lookup, identifiers.get("schema:citation"), canonical_id)


def _build_membership(person_id: str, org_id: str) -> dict[str, Any]:
    membership_id = f"{person_id}_{org_id}"
    return {
        "id": membership_id,
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {
            "pulse:composite": membership_id,
            "uuid": str(uuid4()),
        },
        "idSource": "pulse:composite",
        "org:organization": org_id,
        "org:role": None,
        "time:hasBeginning": None,
        "time:hasEnd": None,
    }


def _build_contribution(person_id: str, repository_id: str) -> dict[str, Any]:
    contribution_id = f"{person_id}_{repository_id}"
    return {
        "id": contribution_id,
        "type": "pulse:Contribution",
        "shacl": "pulse:ContributionShape",
        "identifiers": {
            "pulse:composite": contribution_id,
            "uuid": str(uuid4()),
        },
        "idSource": "pulse:composite",
        "pulse:contributionTo": repository_id,
        "pulse:contributionCount": 0,
        "pulse:firstContributionDate": None,
        "pulse:lastContributionDate": None,
        "schema:author": person_id,
    }


def _extract_affiliation_reference(affiliation: Any) -> str | None:
    if isinstance(affiliation, str):
        return affiliation
    if isinstance(affiliation, dict):
        for key in ("organizationId", "name", "schema:name"):
            value = affiliation.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _stable_uuid_v4_from_seed(seed: str) -> str:
    digest = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(UUID(bytes=bytes(digest)))


def _build_fallback_article_author_person(author_name: str, *, article_id: str) -> dict[str, Any]:
    normalized_name = _normalize_lookup_token(author_name)
    fallback_uuid = _stable_uuid_v4_from_seed(f"{article_id}::{normalized_name}")
    email_local = re.sub(r"[^a-z0-9]+", ".", normalized_name).strip(".")
    if not email_local:
        email_local = "unknown.author"
    fallback_email = f"{email_local}.{fallback_uuid.split('-', maxsplit=1)[0]}@example.org"
    return {
        "id": fallback_uuid,
        "type": "schema:Person",
        "shacl": "pulse:PersonShape",
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": None,
            "uuid": fallback_uuid,
        },
        "idSource": "uuid",
        "schema:name": author_name,
        "schema:email": fallback_email,
        "pulse:githubUsername": None,
        "pulse:orcidIdentifier": None,
        "pulse:infosciencePersonIdentifier": None,
    }


def _detect_repository_fork_cycles(repositories: list[dict[str, Any]]) -> list[str]:
    repository_ids = {
        repository["id"]
        for repository in repositories
        if isinstance(repository.get("id"), str)
    }
    graph = {
        repository["id"]: repository.get("pulse:isForkOf")
        for repository in repositories
        if isinstance(repository.get("id"), str)
        and isinstance(repository.get("pulse:isForkOf"), str)
        and repository.get("pulse:isForkOf") in repository_ids
    }

    warnings: list[str] = []
    visit_state: dict[str, int] = {}
    stack: list[str] = []

    def _visit(node_id: str) -> None:
        visit_state[node_id] = 1
        stack.append(node_id)

        target = graph.get(node_id)
        if isinstance(target, str):
            target_state = visit_state.get(target, 0)
            if target_state == 0:
                _visit(target)
            elif target_state == 1:
                cycle_start_index = stack.index(target)
                cycle_path = " -> ".join([*stack[cycle_start_index:], target])
                warnings.append(f"Circular repository fork reference detected: {cycle_path}")

        stack.pop()
        visit_state[node_id] = 2

    for node in graph:
        if visit_state.get(node, 0) == 0:
            _visit(node)

    return _dedupe_preserve_order(warnings)


def _extract_composite_pair(composite_id: Any) -> tuple[str | None, str | None]:
    if not isinstance(composite_id, str) or "_" not in composite_id:
        return None, None
    left, right = composite_id.split("_", maxsplit=1)
    if not left or not right:
        return None, None
    return left, right


def _normalize_membership_entities(
    memberships: list[dict[str, Any]],
    *,
    person_lookup: dict[str, str],
    organization_lookup: dict[str, str],
) -> tuple[list[dict[str, Any]], set[tuple[str, str]], list[str]]:
    normalized_memberships: list[dict[str, Any]] = []
    covered_pairs: set[tuple[str, str]] = set()
    warnings: list[str] = []
    seen_membership_ids: set[str] = set()

    for membership in memberships:
        composite_id_ref: Any = membership.get("id")
        identifiers = membership.get("identifiers")
        if isinstance(identifiers, dict):
            composite_id_ref = identifiers.get("pulse:composite") or composite_id_ref

        person_ref, org_ref = _extract_composite_pair(composite_id_ref)
        organization_ref = membership.get("org:organization")
        if isinstance(organization_ref, str):
            org_ref = organization_ref

        canonical_person_id = _resolve_lookup_token(person_lookup, person_ref)
        canonical_org_id = _resolve_lookup_token(organization_lookup, org_ref)
        if canonical_person_id is None or canonical_org_id is None:
            warnings.append(
                (
                    "Unresolved class membership reference during reconciliation: "
                    f"id={membership.get('id')}, person={person_ref}, organization={org_ref}"
                ),
            )
            continue

        canonical_membership_id = f"{canonical_person_id}_{canonical_org_id}"
        if canonical_membership_id in seen_membership_ids:
            continue
        seen_membership_ids.add(canonical_membership_id)
        covered_pairs.add((canonical_person_id, canonical_org_id))

        normalized_membership = deepcopy(membership)
        normalized_identifiers = (
            deepcopy(identifiers)
            if isinstance(identifiers, dict)
            else {}
        )
        normalized_identifiers["pulse:composite"] = canonical_membership_id
        uuid_value = normalized_identifiers.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value:
            normalized_identifiers["uuid"] = str(uuid4())

        normalized_membership["id"] = canonical_membership_id
        normalized_membership["type"] = "org:Membership"
        normalized_membership["shacl"] = "pulse:MembershipShape"
        normalized_membership["identifiers"] = normalized_identifiers
        normalized_membership["idSource"] = "pulse:composite"
        normalized_membership["org:organization"] = canonical_org_id
        if not isinstance(normalized_membership.get("org:role"), str):
            normalized_membership["org:role"] = None
        if not isinstance(normalized_membership.get("time:hasBeginning"), str):
            normalized_membership["time:hasBeginning"] = None
        if not isinstance(normalized_membership.get("time:hasEnd"), str):
            normalized_membership["time:hasEnd"] = None

        normalized_memberships.append(normalized_membership)

    return normalized_memberships, covered_pairs, warnings


def _normalize_contribution_entities(  # noqa: C901
    contributions: list[dict[str, Any]],
    *,
    person_lookup: dict[str, str],
    repository_lookup: dict[str, str],
) -> tuple[list[dict[str, Any]], set[tuple[str, str]], list[str]]:
    normalized_contributions: list[dict[str, Any]] = []
    covered_pairs: set[tuple[str, str]] = set()
    warnings: list[str] = []
    seen_contribution_ids: set[str] = set()

    for contribution in contributions:
        composite_id_ref: Any = contribution.get("id")
        identifiers = contribution.get("identifiers")
        if isinstance(identifiers, dict):
            composite_id_ref = identifiers.get("pulse:composite") or composite_id_ref

        person_ref, repository_ref = _extract_composite_pair(composite_id_ref)
        schema_author = contribution.get("schema:author")
        if isinstance(schema_author, str):
            person_ref = schema_author
        contribution_to = contribution.get("pulse:contributionTo")
        if isinstance(contribution_to, str):
            repository_ref = contribution_to

        canonical_person_id = _resolve_lookup_token(person_lookup, person_ref)
        canonical_repository_id = _resolve_lookup_token(repository_lookup, repository_ref)
        if canonical_person_id is None or canonical_repository_id is None:
            warnings.append(
                (
                    "Unresolved class contribution reference during reconciliation: "
                    f"id={contribution.get('id')}, person={person_ref}, "
                    f"repository={repository_ref}"
                ),
            )
            continue

        canonical_contribution_id = f"{canonical_person_id}_{canonical_repository_id}"
        if canonical_contribution_id in seen_contribution_ids:
            continue
        seen_contribution_ids.add(canonical_contribution_id)
        covered_pairs.add((canonical_person_id, canonical_repository_id))

        normalized_contribution = deepcopy(contribution)
        normalized_identifiers = (
            deepcopy(identifiers)
            if isinstance(identifiers, dict)
            else {}
        )
        normalized_identifiers["pulse:composite"] = canonical_contribution_id
        uuid_value = normalized_identifiers.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value:
            normalized_identifiers["uuid"] = str(uuid4())

        contribution_count = normalized_contribution.get("pulse:contributionCount")
        if not isinstance(contribution_count, int) or contribution_count < 0:
            contribution_count = 0

        normalized_contribution["id"] = canonical_contribution_id
        normalized_contribution["type"] = "pulse:Contribution"
        normalized_contribution["shacl"] = "pulse:ContributionShape"
        normalized_contribution["identifiers"] = normalized_identifiers
        normalized_contribution["idSource"] = "pulse:composite"
        normalized_contribution["schema:author"] = canonical_person_id
        normalized_contribution["pulse:contributionTo"] = canonical_repository_id
        normalized_contribution["pulse:contributionCount"] = contribution_count
        if not isinstance(normalized_contribution.get("pulse:firstContributionDate"), str):
            normalized_contribution["pulse:firstContributionDate"] = None
        if not isinstance(normalized_contribution.get("pulse:lastContributionDate"), str):
            normalized_contribution["pulse:lastContributionDate"] = None

        normalized_contributions.append(normalized_contribution)

    return normalized_contributions, covered_pairs, warnings


def _drop_non_shape_fields(organizations: list[dict[str, Any]]) -> None:
    for organization in organizations:
        organization.pop("schema:alternateName", None)


def reconcile_entities(  # noqa: C901, PLR0912, PLR0915
    entities_by_type: dict[str, Any],
    *,
    allow_synthetic_fallbacks: bool = True,
) -> ReconciledEntities:
    reconciled_entities: dict[str, list[dict[str, Any]]] = {
        "persons": deepcopy(_as_entity_list(entities_by_type.get("persons"))),
        "organizations": deepcopy(_as_entity_list(entities_by_type.get("organizations"))),
        "repositories": deepcopy(_as_entity_list(entities_by_type.get("repositories"))),
        "articles": deepcopy(_as_entity_list(entities_by_type.get("articles"))),
    }
    class_memberships = deepcopy(_as_entity_list(entities_by_type.get("memberships")))
    class_contributions = deepcopy(_as_entity_list(entities_by_type.get("contributions")))

    persons = reconciled_entities["persons"]
    organizations = reconciled_entities["organizations"]
    repositories = reconciled_entities["repositories"]
    articles = reconciled_entities["articles"]

    person_lookup: dict[str, str] = {}
    organization_lookup: dict[str, str] = {}
    repository_lookup: dict[str, str] = {}

    for person in persons:
        canonical_id, id_source = resolve_person_id(person)
        person["id"] = canonical_id
        person["idSource"] = id_source
        _normalize_person_identifiers(person)
        _register_person_lookup_tokens(person_lookup, person)
        email = person.get("schema:email")
        if isinstance(email, str):
            person["schema:email"] = anonymize_email(email)

    for organization in organizations:
        _normalize_organization_identifiers(organization)
        canonical_id, id_source = resolve_organization_id(organization)
        organization["id"] = canonical_id
        organization["idSource"] = id_source
        _register_organization_lookup_tokens(organization_lookup, organization)

    for repository in repositories:
        canonical_id, id_source = resolve_repository_id(repository)
        repository["id"] = canonical_id
        repository["idSource"] = id_source
        _register_repository_lookup_tokens(repository_lookup, repository)

    for article in articles:
        canonical_id, id_source = resolve_article_id(article)
        article["id"] = canonical_id
        article["idSource"] = id_source

    _ensure_github_org_units_for_repository_owners(
        organizations=organizations,
        repositories=repositories,
        organization_lookup=organization_lookup,
    )

    fallback_membership_pairs: set[tuple[str, str]] = set()
    fallback_contribution_pairs: set[tuple[str, str]] = set()
    link_warnings = _prune_unresolved_organization_hierarchy_links(
        organizations=organizations,
        organization_lookup=organization_lookup,
    )
    synthesis_warnings: list[str] = []

    for repository in repositories:
        repository_id = repository["id"]
        author_refs_value = repository.get("schema:author")
        author_refs: list[Any]
        if isinstance(author_refs_value, list):
            author_refs = author_refs_value
        else:
            fallback_authors = repository.get("authors")
            author_refs = fallback_authors if isinstance(fallback_authors, list) else []

        canonical_authors: list[str] = []
        unresolved_author_refs: list[str] = []
        for author_ref in author_refs:
            canonical_author = _resolve_lookup_token(person_lookup, author_ref)
            if canonical_author is None:
                if _resolve_lookup_token(organization_lookup, author_ref) is not None:
                    continue
                link_warnings.append(
                    (
                        "Orphan person reference from repository author list: "
                        f"repo={repository_id}, author={author_ref}"
                    ),
                )
                if isinstance(author_ref, str) and author_ref:
                    unresolved_author_refs.append(author_ref)
                continue
            canonical_authors.append(canonical_author)
            fallback_contribution_pairs.add((canonical_author, repository_id))

        canonical_authors = _dedupe_preserve_order(canonical_authors)
        repository["schema:author"] = canonical_authors
        if "authors" in repository:
            repository["authors"] = list(canonical_authors)

        owned_by = repository.get("pulse:ownedBy")
        if isinstance(owned_by, str):
            canonical_owner = _resolve_lookup_token(person_lookup, owned_by) or _resolve_lookup_token(
                organization_lookup,
                owned_by,
            )
            if canonical_owner is None:
                link_warnings.append(
                    (
                        "Orphan owner reference from repository: "
                        f"repo={repository_id}, owner={owned_by}"
                    ),
                )
            else:
                repository["pulse:ownedBy"] = canonical_owner

        fork_of = repository.get("pulse:isForkOf")
        if isinstance(fork_of, str):
            canonical_fork_of = _resolve_lookup_token(repository_lookup, fork_of)
            if canonical_fork_of is None:
                link_warnings.append(
                    (
                        "Orphan repository fork reference: "
                        f"repo={repository_id}, fork={fork_of}"
                    ),
                )
            else:
                repository["pulse:isForkOf"] = canonical_fork_of

    for article in articles:
        article_id = article["id"]
        author_refs_value = article.get("schema:author")
        if isinstance(author_refs_value, list):
            canonical_article_authors: list[str] = []
            for author_ref in author_refs_value:
                canonical_author = _resolve_lookup_token(person_lookup, author_ref)
                if canonical_author is None:
                    if (
                        allow_synthetic_fallbacks
                        and isinstance(author_ref, str)
                        and author_ref
                    ):
                        synthesized_person = _build_fallback_article_author_person(
                            author_ref,
                            article_id=article_id,
                        )
                        _normalize_person_identifiers(synthesized_person)
                        persons.append(synthesized_person)
                        _register_person_lookup_tokens(person_lookup, synthesized_person)
                        canonical_author = synthesized_person["id"]
                        synthesis_warnings.append(
                            (
                                "Synthesized fallback person entity for unresolved article author: "
                                f"article={article_id}, author={author_ref}, person={canonical_author}"
                            ),
                        )
                    else:
                        if allow_synthetic_fallbacks:
                            link_warnings.append(
                                (
                                    "Orphan person reference from article author list: "
                                    f"article={article_id}, author={author_ref}"
                                ),
                            )
                        else:
                            link_warnings.append(
                                (
                                    "Dropped unresolved article author reference because synthetic "
                                    "fallbacks are disabled: "
                                    f"article={article_id}, author={author_ref}"
                                ),
                            )
                        continue
                if canonical_author is None:
                    link_warnings.append(
                        (
                            "Orphan person reference from article author list: "
                            f"article={article_id}, author={author_ref}"
                        ),
                    )
                    continue
                canonical_article_authors.append(canonical_author)
            article["schema:author"] = _dedupe_preserve_order(canonical_article_authors)

        source_org_ref = article.get("schema:sourceOrganization")
        if isinstance(source_org_ref, str):
            canonical_source_org = _resolve_lookup_token(organization_lookup, source_org_ref)
            if canonical_source_org is None:
                link_warnings.append(
                    (
                        "Orphan organization reference from article source organization: "
                        f"article={article_id}, organization={source_org_ref}"
                    ),
                )
            else:
                article["schema:sourceOrganization"] = canonical_source_org

    for person in persons:
        person_id = person["id"]
        affiliations = person.get("affiliations")
        canonical_affiliations: list[str] = []
        if isinstance(affiliations, list):
            for affiliation in affiliations:
                affiliation_ref = _extract_affiliation_reference(affiliation)
                canonical_affiliation = _resolve_lookup_token(organization_lookup, affiliation_ref)
                if canonical_affiliation is None:
                    if isinstance(affiliation_ref, str):
                        link_warnings.append(
                            (
                                "Orphan organization reference from person affiliation: "
                                f"person={person_id}, affiliation={affiliation_ref}"
                            ),
                        )
                    continue
                canonical_affiliations.append(canonical_affiliation)
                fallback_membership_pairs.add((person_id, canonical_affiliation))
            person["affiliations"] = _dedupe_preserve_order(canonical_affiliations)

        owns_refs = person.get("pulse:owns")
        if isinstance(owns_refs, list):
            canonical_owns: list[str] = []
            for owned_repo in owns_refs:
                canonical_repo_id = _resolve_lookup_token(repository_lookup, owned_repo)
                if canonical_repo_id is None:
                    if isinstance(owned_repo, str):
                        link_warnings.append(
                            (
                                "Orphan repository ownership reference from person: "
                                f"person={person_id}, repository={owned_repo}"
                            ),
                        )
                    continue
                canonical_owns.append(canonical_repo_id)
            person["pulse:owns"] = _dedupe_preserve_order(canonical_owns)

    for organization in organizations:
        organization_id = organization["id"]
        owns_refs = organization.get("pulse:owns")
        if isinstance(owns_refs, list):
            canonical_org_owns: list[str] = []
            for owned_repo in owns_refs:
                canonical_repo_id = _resolve_lookup_token(repository_lookup, owned_repo)
                if canonical_repo_id is None:
                    if isinstance(owned_repo, str):
                        link_warnings.append(
                            (
                                "Orphan repository ownership reference from organization: "
                                f"organization={organization_id}, repository={owned_repo}"
                            ),
                        )
                    continue
                canonical_org_owns.append(canonical_repo_id)
            organization["pulse:owns"] = _dedupe_preserve_order(canonical_org_owns)

    memberships, covered_membership_pairs, class_membership_warnings = _normalize_membership_entities(
        class_memberships,
        person_lookup=person_lookup,
        organization_lookup=organization_lookup,
    )
    link_warnings.extend(class_membership_warnings)
    for person_id, org_id in sorted(fallback_membership_pairs):
        if (person_id, org_id) in covered_membership_pairs:
            continue
        if allow_synthetic_fallbacks:
            memberships.append(_build_membership(person_id, org_id))
            covered_membership_pairs.add((person_id, org_id))
            synthesis_warnings.append(
                (
                    "Synthesized fallback membership entity due to missing class-agent link: "
                    f"person={person_id}, organization={org_id}"
                ),
            )
        else:
            link_warnings.append(
                (
                    "Skipped fallback membership synthesis because synthetic fallbacks are disabled: "
                    f"person={person_id}, organization={org_id}"
                ),
            )

    contributions, covered_contribution_pairs, class_contribution_warnings = _normalize_contribution_entities(
        class_contributions,
        person_lookup=person_lookup,
        repository_lookup=repository_lookup,
    )
    link_warnings.extend(class_contribution_warnings)
    for person_id, repository_id in sorted(fallback_contribution_pairs):
        if (person_id, repository_id) in covered_contribution_pairs:
            continue
        if allow_synthetic_fallbacks:
            contributions.append(_build_contribution(person_id, repository_id))
            covered_contribution_pairs.add((person_id, repository_id))
            synthesis_warnings.append(
                (
                    "Synthesized fallback contribution entity due to missing class-agent link: "
                    f"person={person_id}, repository={repository_id}"
                ),
            )
        else:
            link_warnings.append(
                (
                    "Skipped fallback contribution synthesis because synthetic fallbacks are disabled: "
                    f"person={person_id}, repository={repository_id}"
                ),
            )

    membership_ids_by_person: dict[str, list[str]] = {}
    for membership in memberships:
        membership_id = membership["id"]
        person_id, _ = _extract_composite_pair(membership_id)
        if person_id is None:
            continue
        membership_ids_by_person.setdefault(person_id, []).append(membership_id)

    contribution_ids_by_person: dict[str, list[str]] = {}
    for contribution in contributions:
        contribution_id = contribution["id"]
        person_id, _ = _extract_composite_pair(contribution_id)
        if person_id is None:
            continue
        contribution_ids_by_person.setdefault(person_id, []).append(contribution_id)

    for person in persons:
        person_id = person["id"]
        person["org:hasMembership"] = _dedupe_preserve_order(
            membership_ids_by_person.get(person_id, []),
        )
        person["pulse:hasContribution"] = _dedupe_preserve_order(
            contribution_ids_by_person.get(person_id, []),
        )

    link_warnings.extend(_detect_repository_fork_cycles(repositories))
    _drop_non_shape_fields(organizations)

    return ReconciledEntities(
        entities=reconciled_entities,
        memberships=memberships,
        contributions=contributions,
        link_warnings=_dedupe_preserve_order(link_warnings),
        synthesis_warnings=_dedupe_preserve_order(synthesis_warnings),
    )
