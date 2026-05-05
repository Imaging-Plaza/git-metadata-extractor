from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from typing import Any
from uuid import uuid4

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
ORG_ID_SOURCE_PRIORITY: dict[str, int] = {
    "pulse:ror": 4,
    "pulse:infoscienceOrganizationIdentifier": 3,
    "pulse:githubOrganizationHandle": 2,
    "uuid": 1,
}
DEBUG_SAMPLE_LIMIT = 10
ORGANIZATION_NAME_EQUIVALENCE_TOKENS: dict[str, str] = {
    "centre": "center",
    "centres": "centers",
}
GITHUB_ORG_BASE_URI = "https://github.com/"
GITHUB_HANDLE_PATTERN = re.compile(
    r"^[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}$",
)


def _normalize_github_org_handle(value: Any, *, allow_plain_handle: bool = True) -> str | None:
    if not isinstance(value, str):
        return None
    github_handle = value.strip()
    if not github_handle:
        return None
    is_github_url = github_handle.lower().startswith(GITHUB_ORG_BASE_URI)
    if is_github_url:
        github_handle = github_handle[len(GITHUB_ORG_BASE_URI) :]
        github_handle = github_handle.split("/", maxsplit=1)[0]
    elif not allow_plain_handle:
        return None

    if github_handle.startswith("@"):
        github_handle = github_handle[1:]
    if not github_handle:
        return None
    if GITHUB_HANDLE_PATTERN.fullmatch(github_handle) is None:
        return None
    return github_handle


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
    normalized_ror: str | None = None
    raw_ror = (
        normalized_identifiers.get("pulse:ror")
        or organization.get("pulse:ror")
        or organization.get("schema:identifier")
    )
    if isinstance(raw_ror, str) and raw_ror.strip():
        ror_candidate = raw_ror.strip()
        if ror_candidate.lower().startswith("https://ror.org/"):
            ror_candidate = ror_candidate.rsplit("/", maxsplit=1)[-1]
        ror_token = ror_candidate.lower()
        if re.fullmatch(r"[0-9a-z]{9}", ror_token):
            normalized_ror = f"https://ror.org/{ror_token}"

    normalized_infoscience_id = _normalize_infoscience_uuid(
        normalized_identifiers.get("pulse:infoscienceOrganizationIdentifier")
        or organization.get("pulse:infoscienceOrganizationIdentifier"),
    )
    normalized_github_handle: str | None = None
    for candidate in (
        normalized_identifiers.get("pulse:githubOrganizationHandle"),
        organization.get("pulse:githubOrganizationHandle"),
    ):
        normalized_candidate = _normalize_github_org_handle(
            candidate,
            allow_plain_handle=True,
        )
        if isinstance(normalized_candidate, str):
            normalized_github_handle = normalized_candidate
            break

    if normalized_github_handle is None:
        for candidate in (
            organization.get("id"),
            organization.get("schema:url"),
        ):
            normalized_candidate = _normalize_github_org_handle(
                candidate,
                allow_plain_handle=False,
            )
            if isinstance(normalized_candidate, str):
                normalized_github_handle = normalized_candidate
                break

    if normalized_github_handle is not None:
        normalized_github_handle = normalized_github_handle.strip()

    uuid_value = _normalize_uuid_v4(normalized_identifiers.get("uuid"))
    if uuid_value is None:
        uuid_value = str(uuid4())

    normalized_identifiers["pulse:ror"] = normalized_ror
    normalized_identifiers["pulse:infoscienceOrganizationIdentifier"] = normalized_infoscience_id
    normalized_identifiers["pulse:githubOrganizationHandle"] = normalized_github_handle
    normalized_identifiers["uuid"] = uuid_value
    organization["identifiers"] = normalized_identifiers
    organization["pulse:infoscienceOrganizationIdentifier"] = normalized_infoscience_id
    organization["pulse:githubOrganizationHandle"] = normalized_github_handle
    if normalized_ror is not None:
        organization["schema:identifier"] = normalized_ror


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


def _organization_source_priority(organization: dict[str, Any]) -> int:
    id_source = organization.get("idSource")
    if isinstance(id_source, str):
        return ORG_ID_SOURCE_PRIORITY.get(id_source, 0)
    if isinstance(_organization_normalized_ror(organization), str):
        return ORG_ID_SOURCE_PRIORITY["pulse:ror"]
    if isinstance(_organization_infoscience_uuid(organization), str):
        return ORG_ID_SOURCE_PRIORITY["pulse:infoscienceOrganizationIdentifier"]
    if isinstance(_organization_github_handle_key(organization), str):
        return ORG_ID_SOURCE_PRIORITY["pulse:githubOrganizationHandle"]
    return ORG_ID_SOURCE_PRIORITY["uuid"]


def _organization_identifier_count(organization: dict[str, Any]) -> int:
    identifiers = organization.get("identifiers")
    if not isinstance(identifiers, dict):
        return 0
    return sum(
        1
        for key in (
            "pulse:ror",
            "pulse:infoscienceOrganizationIdentifier",
            "pulse:githubOrganizationHandle",
            "uuid",
        )
        if isinstance(identifiers.get(key), str) and identifiers.get(key)
    )


def _organization_metadata_score(organization: dict[str, Any]) -> int:
    score = 0
    for field in (
        "schema:name",
        "schema:identifier",
        "pulse:OrganizationType",
        "pulse:githubOrganizationHandle",
        "pulse:infoscienceOrganizationIdentifier",
    ):
        value = organization.get(field)
        if isinstance(value, str) and value:
            score += 1
    followers = organization.get("pulse:githubOrgFollowers")
    if isinstance(followers, int):
        score += 1
    for field in ("aliases", "acronyms", "labels", "org:hasUnit", "org:unitOf", "pulse:owns"):
        value = organization.get(field)
        if isinstance(value, list) and value:
            score += 1
    return score


def _organization_preference_tuple(organization: dict[str, Any]) -> tuple[int, int, int]:
    return (
        _organization_source_priority(organization),
        _organization_identifier_count(organization),
        _organization_metadata_score(organization),
    )


def _normalize_ror_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.lower().startswith("https://ror.org/"):
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    candidate = candidate.lower()
    if not re.fullmatch(r"[0-9a-z]{9}", candidate):
        return None
    return f"https://ror.org/{candidate}"


def _organization_normalized_ror(organization: dict[str, Any]) -> str | None:
    identifiers = organization.get("identifiers")
    if isinstance(identifiers, dict):
        normalized = _normalize_ror_url(identifiers.get("pulse:ror"))
        if normalized is not None:
            return normalized
    for candidate in (
        organization.get("schema:identifier"),
    ):
        normalized = _normalize_ror_url(candidate)
        if normalized is not None:
            return normalized
    return None


def _organization_infoscience_uuid(organization: dict[str, Any]) -> str | None:
    identifiers = organization.get("identifiers")
    if isinstance(identifiers, dict):
        normalized = _normalize_infoscience_uuid(
            identifiers.get("pulse:infoscienceOrganizationIdentifier"),
        )
        if normalized is not None:
            return normalized
    return _normalize_infoscience_uuid(organization.get("pulse:infoscienceOrganizationIdentifier"))


def _organization_github_handle_key(organization: dict[str, Any]) -> str | None:
    handle = _organization_github_handle(organization)
    if isinstance(handle, str) and handle:
        return handle.casefold()
    return None


def _organization_name_key(organization: dict[str, Any]) -> str | None:
    name = organization.get("schema:name")
    if isinstance(name, str) and name:
        normalized = _normalize_organization_name_for_equivalence(name)
        return normalized or None
    return None


def _normalize_organization_name_for_equivalence(value: str) -> str:
    normalized = normalize_string(value)
    if not normalized:
        return ""
    normalized_tokens = [
        ORGANIZATION_NAME_EQUIVALENCE_TOKENS.get(token, token)
        for token in normalized.split()
    ]
    return " ".join(normalized_tokens)


def _dedupe_any_list(values: list[Any]) -> list[Any]:
    deduplicated: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(value)
        if isinstance(value, dict):
            marker = f"dict:{repr(sorted(value.items()))}"
        if marker in seen:
            continue
        deduplicated.append(value)
        seen.add(marker)
    return deduplicated


def _merge_organization_payload(
    canonical: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    canonical_identifiers = (
        deepcopy(canonical.get("identifiers"))
        if isinstance(canonical.get("identifiers"), dict)
        else {}
    )
    candidate_identifiers = (
        deepcopy(candidate.get("identifiers"))
        if isinstance(candidate.get("identifiers"), dict)
        else {}
    )
    for key in (
        "pulse:ror",
        "pulse:infoscienceOrganizationIdentifier",
        "pulse:githubOrganizationHandle",
        "uuid",
    ):
        if isinstance(canonical_identifiers.get(key), str) and canonical_identifiers.get(key):
            continue
        fallback_value = candidate_identifiers.get(key) or candidate.get(key)
        if isinstance(fallback_value, str) and fallback_value:
            canonical_identifiers[key] = fallback_value
    canonical["identifiers"] = canonical_identifiers

    for key in ("pulse:infoscienceOrganizationIdentifier", "pulse:githubOrganizationHandle"):
        value = canonical_identifiers.get(key)
        if isinstance(value, str) and value:
            canonical[key] = value
        elif key not in canonical:
            canonical[key] = None

    canonical_ror = _organization_normalized_ror(canonical)
    if canonical_ror is not None:
        canonical["schema:identifier"] = canonical_ror
    elif canonical.get("schema:identifier") is None and isinstance(
        candidate.get("schema:identifier"),
        str,
    ):
        canonical["schema:identifier"] = candidate["schema:identifier"]

    for field in ("schema:name", "pulse:OrganizationType"):
        if isinstance(canonical.get(field), str) and canonical.get(field):
            continue
        candidate_value = candidate.get(field)
        if isinstance(candidate_value, str) and candidate_value:
            canonical[field] = candidate_value

    if not isinstance(canonical.get("pulse:githubOrgFollowers"), int) and isinstance(
        candidate.get("pulse:githubOrgFollowers"),
        int,
    ):
        canonical["pulse:githubOrgFollowers"] = candidate["pulse:githubOrgFollowers"]

    for field in (
        "aliases",
        "acronyms",
        "labels",
        "org:hasUnit",
        "org:unitOf",
        "pulse:owns",
    ):
        merged_values: list[Any] = []
        existing_values = canonical.get(field)
        if isinstance(existing_values, list):
            merged_values.extend(existing_values)
        candidate_values = candidate.get(field)
        if isinstance(candidate_values, list):
            merged_values.extend(candidate_values)
        canonical[field] = _dedupe_any_list(merged_values)

    if not isinstance(canonical.get("type"), str) or not canonical.get("type"):
        canonical["type"] = "org:Organization"
    if not isinstance(canonical.get("shacl"), str) or not canonical.get("shacl"):
        canonical["shacl"] = "pulse:OrganizationShape"


def _organization_equivalence_groups(
    organizations: list[dict[str, Any]],
) -> list[list[int]]:
    count = len(organizations)
    if count <= 1:
        return []

    parent = list(range(count))

    def _find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def _union(left: int, right: int) -> None:
        left_root = _find(left)
        right_root = _find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    by_ror: dict[str, list[int]] = defaultdict(list)
    by_infoscience: dict[str, list[int]] = defaultdict(list)
    by_handle: dict[str, list[int]] = defaultdict(list)
    for index, organization in enumerate(organizations):
        ror = _organization_normalized_ror(organization)
        if isinstance(ror, str):
            by_ror[ror].append(index)
        infoscience_id = _organization_infoscience_uuid(organization)
        if isinstance(infoscience_id, str):
            by_infoscience[infoscience_id].append(index)
        github_handle = _organization_github_handle_key(organization)
        if isinstance(github_handle, str):
            by_handle[github_handle].append(index)

    for indexed_groups in (by_ror, by_infoscience, by_handle):
        for indices in indexed_groups.values():
            if len(indices) <= 1:
                continue
            head = indices[0]
            for member in indices[1:]:
                _union(head, member)

    for left in range(count):
        left_org = organizations[left]
        left_ror = _organization_normalized_ror(left_org)
        left_infoscience = _organization_infoscience_uuid(left_org)
        left_name = _organization_name_key(left_org)
        left_handle = _organization_github_handle_key(left_org)
        for right in range(left + 1, count):
            right_org = organizations[right]
            right_ror = _organization_normalized_ror(right_org)
            right_infoscience = _organization_infoscience_uuid(right_org)
            has_cross_source_pair = (
                (isinstance(left_ror, str) and isinstance(right_infoscience, str))
                or (isinstance(right_ror, str) and isinstance(left_infoscience, str))
            )
            if not has_cross_source_pair:
                continue
            right_name = _organization_name_key(right_org)
            names_match = (
                isinstance(left_name, str)
                and isinstance(right_name, str)
                and left_name == right_name
            )
            right_handle = _organization_github_handle_key(right_org)
            handles_match = (
                isinstance(left_handle, str)
                and isinstance(right_handle, str)
                and left_handle == right_handle
            )
            if names_match or handles_match:
                _union(left, right)

    grouped: dict[int, list[int]] = defaultdict(list)
    for index in range(count):
        grouped[_find(index)].append(index)

    merged_groups = [indices for indices in grouped.values() if len(indices) > 1]
    merged_groups.sort(key=lambda indices: min(indices))
    return merged_groups


def _merge_equivalent_organizations(
    organizations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], int, int]:
    groups = _organization_equivalence_groups(organizations)
    if not groups:
        return organizations, {}, 0, 0

    group_by_index: dict[int, list[int]] = {}
    for group in groups:
        for index in group:
            group_by_index[index] = group

    merged_organizations: list[dict[str, Any]] = []
    consumed_indices: set[int] = set()
    id_remap: dict[str, str] = {}
    merged_group_count = 0
    merged_entity_count = 0

    for index, organization in enumerate(organizations):
        if index in consumed_indices:
            continue

        group = group_by_index.get(index)
        if not isinstance(group, list):
            merged_organizations.append(organization)
            continue

        merged_group_count += 1
        winner_index = max(
            group,
            key=lambda candidate_index: (
                *_organization_preference_tuple(organizations[candidate_index]),
                -candidate_index,
            ),
        )
        merged = deepcopy(organizations[winner_index])
        winner_id = merged.get("id")

        for member_index in group:
            consumed_indices.add(member_index)
            member = organizations[member_index]
            member_id = member.get("id")
            if (
                member_index != winner_index
                and isinstance(member_id, str)
                and member_id
                and isinstance(winner_id, str)
                and winner_id
                and member_id != winner_id
            ):
                id_remap[member_id] = winner_id
                merged_entity_count += 1
            if member_index == winner_index:
                continue
            _merge_organization_payload(merged, member)

        merged_organizations.append(merged)

    return merged_organizations, id_remap, merged_group_count, merged_entity_count


def _apply_organization_id_remap(
    candidate: Any,
    org_id_remap: dict[str, str],
) -> str | Any:
    if isinstance(candidate, str):
        return org_id_remap.get(candidate, candidate)
    return candidate


def _apply_org_remap_to_entities(
    *,
    organizations: list[dict[str, Any]],
    repositories: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    persons: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
    org_id_remap: dict[str, str],
) -> None:
    if not org_id_remap:
        return

    for repository in repositories:
        owner = repository.get("pulse:ownedBy")
        if isinstance(owner, str):
            repository["pulse:ownedBy"] = _apply_organization_id_remap(owner, org_id_remap)

    for article in articles:
        source_org = article.get("schema:sourceOrganization")
        if isinstance(source_org, str):
            article["schema:sourceOrganization"] = _apply_organization_id_remap(
                source_org,
                org_id_remap,
            )

    for person in persons:
        affiliations = person.get("affiliations")
        if not isinstance(affiliations, list):
            continue
        remapped_affiliations: list[Any] = []
        for affiliation in affiliations:
            if isinstance(affiliation, str):
                remapped_affiliations.append(
                    _apply_organization_id_remap(affiliation, org_id_remap),
                )
                continue
            if isinstance(affiliation, dict):
                remapped_affiliation = deepcopy(affiliation)
                organization_id = remapped_affiliation.get("organizationId")
                if isinstance(organization_id, str):
                    remapped_affiliation["organizationId"] = _apply_organization_id_remap(
                        organization_id,
                        org_id_remap,
                    )
                remapped_affiliations.append(remapped_affiliation)
                continue
            remapped_affiliations.append(affiliation)
        person["affiliations"] = remapped_affiliations

    for membership in memberships:
        organization_id = membership.get("org:organization")
        if isinstance(organization_id, str):
            membership["org:organization"] = _apply_organization_id_remap(
                organization_id,
                org_id_remap,
            )

    for organization in organizations:
        has_units = organization.get("org:hasUnit")
        if isinstance(has_units, list):
            organization["org:hasUnit"] = _dedupe_preserve_order(
                [
                    _apply_organization_id_remap(has_unit, org_id_remap)
                    for has_unit in has_units
                    if isinstance(has_unit, str) and has_unit
                ],
            )
        unit_of = organization.get("org:unitOf")
        if isinstance(unit_of, str):
            unit_of = [unit_of]
        if isinstance(unit_of, list):
            organization["org:unitOf"] = _dedupe_preserve_order(
                [
                    _apply_organization_id_remap(parent_id, org_id_remap)
                    for parent_id in unit_of
                    if isinstance(parent_id, str) and parent_id
                ],
            )


def _organization_lookup_tokens(organization: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for value in (
        organization.get("id"),
        organization.get("schema:name"),
        organization.get("schema:identifier"),
    ):
        if not isinstance(value, str):
            continue
        tokens.extend(_lookup_token_variants(value))

    handle = _organization_github_handle(organization)
    if isinstance(handle, str) and handle:
        tokens.extend(_lookup_token_variants(handle))
        tokens.extend(_lookup_token_variants(f"@{handle}"))

    for key in ("aliases", "acronyms"):
        values = organization.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str):
                tokens.extend(_lookup_token_variants(value))

    labels = organization.get("labels")
    if isinstance(labels, list):
        for label_payload in labels:
            label = label_payload
            if isinstance(label_payload, dict):
                label = label_payload.get("label")
            if isinstance(label, str):
                tokens.extend(_lookup_token_variants(label))

    identifiers = organization.get("identifiers")
    if isinstance(identifiers, dict):
        for value in (
            identifiers.get("pulse:ror"),
            identifiers.get("pulse:infoscienceOrganizationIdentifier"),
            identifiers.get("pulse:githubOrganizationHandle"),
            identifiers.get("uuid"),
        ):
            if isinstance(value, str):
                tokens.extend(_lookup_token_variants(value))

    return _dedupe_preserve_order(tokens)


def _preferred_organization_id(
    *,
    existing_id: str,
    candidate_id: str,
    organizations_by_id: dict[str, dict[str, Any]],
) -> str:
    existing_org = organizations_by_id.get(existing_id)
    candidate_org = organizations_by_id.get(candidate_id)
    if not isinstance(existing_org, dict):
        return candidate_id
    if not isinstance(candidate_org, dict):
        return existing_id

    existing_rank = _organization_preference_tuple(existing_org)
    candidate_rank = _organization_preference_tuple(candidate_org)
    if candidate_rank > existing_rank:
        return candidate_id
    return existing_id


def _github_org_account_id(handle: str) -> str:
    return f"{GITHUB_ORG_BASE_URI}{handle}"


def _prefer_github_unit_for_handle_token(
    *,
    token: str,
    existing_id: str,
    candidate_id: str,
    organizations_by_id: dict[str, dict[str, Any]],
) -> str | None:
    existing_org = organizations_by_id.get(existing_id)
    candidate_org = organizations_by_id.get(candidate_id)
    if not isinstance(existing_org, dict) or not isinstance(candidate_org, dict):
        return None

    existing_handle = _organization_github_handle_key(existing_org)
    candidate_handle = _organization_github_handle_key(candidate_org)
    if (
        not isinstance(existing_handle, str)
        or not isinstance(candidate_handle, str)
        or existing_handle != candidate_handle
    ):
        return None

    if token not in _lookup_token_variants(existing_handle):
        return None

    existing_is_github_unit = existing_org.get("idSource") == "pulse:githubOrganizationHandle"
    candidate_is_github_unit = candidate_org.get("idSource") == "pulse:githubOrganizationHandle"
    if existing_is_github_unit == candidate_is_github_unit:
        return None

    return existing_id if existing_is_github_unit else candidate_id


def _strip_organization_lookup_fields(organizations: list[dict[str, Any]]) -> None:
    """Drop reconciliation-only lookup fields not allowed by strict schema."""
    for organization in organizations:
        for field in ("aliases", "acronyms", "labels"):
            organization.pop(field, None)


def _build_organization_lookup_with_collisions(
    organizations: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    lookup: dict[str, str] = {}
    collision_records: list[dict[str, str]] = []
    seen_collision_markers: set[tuple[str, str, str]] = set()

    organizations_by_id = {
        organization["id"]: organization
        for organization in organizations
        if isinstance(organization.get("id"), str) and organization.get("id")
    }

    for organization in organizations:
        canonical_id = organization.get("id")
        if not isinstance(canonical_id, str) or not canonical_id:
            continue
        for token in _organization_lookup_tokens(organization):
            existing_id = lookup.get(token)
            if existing_id is None:
                lookup[token] = canonical_id
                continue
            if existing_id == canonical_id:
                continue
            if token == existing_id:
                continue
            if token == canonical_id:
                lookup[token] = canonical_id
                marker = (token, canonical_id, existing_id)
                if marker not in seen_collision_markers:
                    seen_collision_markers.add(marker)
                    collision_records.append(
                        {
                            "token": token,
                            "preferred_id": canonical_id,
                            "alternate_id": existing_id,
                        },
                    )
                continue
            github_unit_preferred_id = _prefer_github_unit_for_handle_token(
                token=token,
                existing_id=existing_id,
                candidate_id=canonical_id,
                organizations_by_id=organizations_by_id,
            )
            if isinstance(github_unit_preferred_id, str):
                lookup[token] = github_unit_preferred_id
                alternate_id = canonical_id if github_unit_preferred_id == existing_id else existing_id
                marker = (token, github_unit_preferred_id, alternate_id)
                if marker not in seen_collision_markers:
                    seen_collision_markers.add(marker)
                    collision_records.append(
                        {
                            "token": token,
                            "preferred_id": github_unit_preferred_id,
                            "alternate_id": alternate_id,
                        },
                    )
                continue
            preferred_id = _preferred_organization_id(
                existing_id=existing_id,
                candidate_id=canonical_id,
                organizations_by_id=organizations_by_id,
            )
            lookup[token] = preferred_id
            alternate_id = canonical_id if preferred_id == existing_id else existing_id
            marker = (token, preferred_id, alternate_id)
            if marker in seen_collision_markers:
                continue
            seen_collision_markers.add(marker)
            collision_records.append(
                {
                    "token": token,
                    "preferred_id": preferred_id,
                    "alternate_id": alternate_id,
                },
            )

    return lookup, collision_records


def _build_github_org_account_unit(
    *,
    github_handle: str,
    parent_org_id: str,
) -> dict[str, Any]:
    github_org_id = _github_org_account_id(github_handle)
    return {
        "id": github_org_id,
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
        "org:unitOf": [parent_org_id] if isinstance(parent_org_id, str) and parent_org_id else [],
        "pulse:owns": [],
    }


def _ensure_github_org_units_for_repository_owners(
    *,
    organizations: list[dict[str, Any]],
    repositories: list[dict[str, Any]],
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

        github_org_id = _github_org_account_id(github_handle)
        org_units = organization.get("org:hasUnit")
        if not isinstance(org_units, list):
            org_units = []
        organization["org:hasUnit"] = _dedupe_preserve_order(
            [
                *[
                    github_org_id if value == github_handle else value
                    for value in org_units
                    if isinstance(value, str) and value
                ],
                github_org_id,
            ],
        )

        github_unit = organizations_by_id.get(github_org_id)
        legacy_github_unit = organizations_by_id.get(github_handle)
        if github_unit is None and isinstance(legacy_github_unit, dict):
            legacy_github_unit["id"] = github_org_id
            organizations_by_id.pop(github_handle, None)
            organizations_by_id[github_org_id] = legacy_github_unit
            github_unit = legacy_github_unit
        if github_unit is None:
            github_unit = _build_github_org_account_unit(
                github_handle=github_handle,
                parent_org_id=canonical_org_id,
            )
            organizations.append(github_unit)
            organizations_by_id[github_org_id] = github_unit

        github_unit["org:unitOf"] = [canonical_org_id]
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

        unit_of_refs = organization.get("org:unitOf")
        if isinstance(unit_of_refs, str):
            unit_of_refs = [unit_of_refs]
        if not isinstance(unit_of_refs, list) or not unit_of_refs:
            organization["org:unitOf"] = []
            continue

        canonical_unit_of: list[str] = []
        for ref in unit_of_refs:
            if not isinstance(ref, str) or not ref:
                continue
            resolved = _resolve_lookup_token(organization_lookup, ref)
            if resolved is None or resolved not in organization_ids:
                dropped_unit_of += 1
                continue
            canonical_unit_of.append(resolved)
        organization["org:unitOf"] = _dedupe_preserve_order(canonical_unit_of)

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


def _extract_affiliation_reference(affiliation: Any) -> str | None:
    if isinstance(affiliation, str):
        return affiliation
    if isinstance(affiliation, dict):
        for key in ("organizationId", "name", "schema:name"):
            value = affiliation.get(key)
            if isinstance(value, str) and value:
                return value
    return None


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
        normalized_membership["_person_ref"] = canonical_person_id
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


def reconcile_entities(  # noqa: C901, PLR0912, PLR0915
    entities_by_type: dict[str, Any],
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
    repository_lookup: dict[str, str] = {}
    link_warnings: list[str] = []

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
        existing_id_source = organization.get("idSource")
        _normalize_organization_identifiers(organization)
        canonical_id, id_source = resolve_organization_id(organization)
        organization["id"] = canonical_id
        organization["idSource"] = id_source
        if isinstance(existing_id_source, str) and existing_id_source != id_source:
            link_warnings.append(
                (
                    "Organization ID hierarchy override during reconciliation: "
                    f"name={organization.get('schema:name')}, "
                    f"from={existing_id_source}, to={id_source}, id={canonical_id}"
                ),
            )

    for repository in repositories:
        canonical_id, id_source = resolve_repository_id(repository)
        repository["id"] = canonical_id
        repository["idSource"] = id_source
        _register_repository_lookup_tokens(repository_lookup, repository)

    for article in articles:
        canonical_id, id_source = resolve_article_id(article)
        article["id"] = canonical_id
        article["idSource"] = id_source

    organizations, organization_id_remap, merged_group_count, merged_entity_count = (
        _merge_equivalent_organizations(organizations)
    )
    reconciled_entities["organizations"] = organizations
    _apply_org_remap_to_entities(
        organizations=organizations,
        repositories=repositories,
        articles=articles,
        persons=persons,
        memberships=class_memberships,
        org_id_remap=organization_id_remap,
    )

    _ensure_github_org_units_for_repository_owners(
        organizations=organizations,
        repositories=repositories,
    )
    organization_lookup, token_collision_records = _build_organization_lookup_with_collisions(
        organizations,
    )
    if token_collision_records:
        token_collision_samples = token_collision_records[:DEBUG_SAMPLE_LIMIT]
        sample_text = "; ".join(
            (
                f"token='{sample['token']}' preferred={sample['preferred_id']} "
                f"alternate={sample['alternate_id']}"
            )
            for sample in token_collision_samples
        )
        link_warnings.append(
            (
                "Ambiguous organization lookup tokens detected during reconciliation: "
                f"count={len(token_collision_records)}. "
                "Preferred ROR-backed canonical organizations when available. "
                f"Samples: {sample_text}"
            ),
        )

    link_warnings.extend(
        _prune_unresolved_organization_hierarchy_links(
            organizations=organizations,
            organization_lookup=organization_lookup,
        ),
    )

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

    organization_ids = {
        organization["id"]
        for organization in organizations
        if isinstance(organization.get("id"), str)
    }
    owned_repository_ids_by_org: dict[str, list[str]] = defaultdict(list)
    for repository in repositories:
        repository_id = repository.get("id")
        owner_ref = repository.get("pulse:ownedBy")
        if not isinstance(repository_id, str) or not repository_id:
            continue
        if not isinstance(owner_ref, str) or not owner_ref:
            continue
        canonical_owner = _resolve_lookup_token(organization_lookup, owner_ref)
        if (
            isinstance(canonical_owner, str)
            and canonical_owner in organization_ids
        ):
            owned_repository_ids_by_org[canonical_owner].append(repository_id)

    # NOTE: Ownership propagation from GitHub org-account units to canonical
    # parent organizations is intentionally disabled. `pulse:owns` should reflect
    # only direct repository owners resolved from `repository.pulse:ownedBy`.
    #
    # If needed in the future, re-enable propagation only for strict same-handle
    # parent/child pairs and with explicit semantic approval.

    for organization in organizations:
        organization_id = organization["id"]
        github_handle = _organization_github_handle(organization)
        if not isinstance(github_handle, str) or not github_handle:
            organization["pulse:owns"] = []
            continue
        organization["pulse:owns"] = _dedupe_preserve_order(
            owned_repository_ids_by_org.get(organization_id, []),
        )

    memberships, _covered_membership_pairs, class_membership_warnings = _normalize_membership_entities(
        class_memberships,
        person_lookup=person_lookup,
        organization_lookup=organization_lookup,
    )
    link_warnings.extend(class_membership_warnings)

    contributions, _covered_contribution_pairs, class_contribution_warnings = _normalize_contribution_entities(
        class_contributions,
        person_lookup=person_lookup,
        repository_lookup=repository_lookup,
    )
    link_warnings.extend(class_contribution_warnings)

    membership_ids_by_person: dict[str, list[str]] = {}
    for membership in memberships:
        membership_id = membership["id"]
        person_id = membership.get("_person_ref")
        if person_id is None:
            continue
        membership_ids_by_person.setdefault(person_id, []).append(membership_id)

    contribution_ids_by_person: dict[str, list[str]] = {}
    for contribution in contributions:
        contribution_id = contribution["id"]
        person_id = contribution.get("schema:author")
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

    _strip_organization_lookup_fields(organizations)

    link_warnings.extend(_detect_repository_fork_cycles(repositories))
    org_remap_entries = [
        {"from": source_id, "to": target_id}
        for source_id, target_id in sorted(organization_id_remap.items())
        if source_id != target_id
    ]
    reconciliation_debug = {
        "merged_group_count": merged_group_count,
        "merged_entity_count": merged_entity_count,
        "org_remap_count": len(org_remap_entries),
        "org_remap_sample": org_remap_entries[:DEBUG_SAMPLE_LIMIT],
        "token_collision_count": len(token_collision_records),
        "token_collision_sample": token_collision_records[:DEBUG_SAMPLE_LIMIT],
    }
    return ReconciledEntities(
        entities=reconciled_entities,
        memberships=memberships,
        contributions=contributions,
        link_warnings=_dedupe_preserve_order(link_warnings),
        reconciliation_debug=reconciliation_debug,
    )
