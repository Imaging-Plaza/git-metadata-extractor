from __future__ import annotations

import json
import re
import uuid
from typing import Any

INFOSCIENCE_CORE_ITEMS_BASE_URI = "https://infoscience.epfl.ch/server/api/core/items/"
INFOSCIENCE_PERSON_BASE_URI = INFOSCIENCE_CORE_ITEMS_BASE_URI
INFOSCIENCE_ORGANIZATION_BASE_URI = INFOSCIENCE_CORE_ITEMS_BASE_URI
INFOSCIENCE_PUBLICATION_BASE_URI = INFOSCIENCE_CORE_ITEMS_BASE_URI
GITHUB_BASE_URI = "https://github.com/"
ORCID_BASE_URI = "https://orcid.org/"
ROR_BASE_URI = "https://ror.org/"
DOI_BASE_URI = "https://doi.org/"

PERSON_UUID_NAMESPACE = uuid.UUID("bfc0a4f9-2ef5-59eb-9bf8-76ef425de91e")
ORGANIZATION_UUID_NAMESPACE = uuid.UUID("4f7f847a-8d6e-56b3-9164-2668e51f040f")
REPOSITORY_UUID_NAMESPACE = uuid.UUID("c5b462e3-9cf5-5b59-b2df-bdbfd9bd0ac5")
ARTICLE_UUID_NAMESPACE = uuid.UUID("f9f26cbf-1939-5c0d-a0f2-89dcf8a56cf8")

PERSON_ID_SOURCES = {
    "pulse:orcid",
    "pulse:infosciencePersonIdentifier",
    "pulse:githubUsername",
    "uuid",
}
ORGANIZATION_ID_SOURCES = {
    "pulse:ror",
    "pulse:infoscienceOrganizationIdentifier",
    "pulse:githubOrganizationHandle",
    "uuid",
}
REPOSITORY_ID_SOURCES = {
    "pulse:githubRepositoryHandle",
    "schema:identifier",
    "uuid",
}
ARTICLE_ID_SOURCES = {
    "schema:identifier",
    "pulse:infoscienceArticleIdentifier",
    "uuid",
}
ID_SOURCE_ALIASES = {
    "orcid": "pulse:orcid",
    "infosciencePersonIdentifier": "pulse:infosciencePersonIdentifier",
    "githubUsername": "pulse:githubUsername",
    "ror": "pulse:ror",
    "infoscienceOrganizationIdentifier": "pulse:infoscienceOrganizationIdentifier",
    "githubOrganizationHandle": "pulse:githubOrganizationHandle",
    "githubRepositoryHandle": "pulse:githubRepositoryHandle",
    "doi": "schema:identifier",
    "infoscienceArticleIdentifier": "pulse:infoscienceArticleIdentifier",
}
REPOSITORY_HANDLE_PARTS = 2


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None


def _lookup_identifier(entity: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    identifiers = entity.get("identifiers")
    for key in keys:
        direct = _clean_text(entity.get(key))
        if direct is not None:
            return direct

        if isinstance(identifiers, dict):
            nested = _clean_text(identifiers.get(key))
            if nested is not None:
                return nested

    return None


def _normalize_uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        return None


def _existing_resolution(
    entity: dict[str, Any],
    valid_sources: set[str],
) -> tuple[str, str] | None:
    entity_id = _clean_text(entity.get("id"))
    id_source = _clean_text(entity.get("idSource"))
    if entity_id is None or id_source is None:
        return None
    normalized_source = ID_SOURCE_ALIASES.get(id_source, id_source)
    if normalized_source not in valid_sources:
        return None
    normalized_id: str | None = None

    if normalized_source == "pulse:orcid":
        normalized_orcid = _normalize_orcid(entity_id)
        if normalized_orcid is not None:
            normalized_id = f"{ORCID_BASE_URI}{normalized_orcid}"
    elif normalized_source == "pulse:infosciencePersonIdentifier":
        normalized_infoscience_id = _normalize_infoscience_identifier(
            entity_id,
            entity_path="person",
            legacy_path="person",
        )
        if normalized_infoscience_id is not None:
            normalized_id = f"{INFOSCIENCE_PERSON_BASE_URI}{normalized_infoscience_id}"
    elif normalized_source == "pulse:githubUsername":
        normalized_github_username = _normalize_github_handle(entity_id)
        if normalized_github_username is not None:
            normalized_id = f"{GITHUB_BASE_URI}{normalized_github_username}"
    elif normalized_source == "pulse:ror":
        normalized_ror = _normalize_ror(entity_id)
        if normalized_ror is not None:
            normalized_id = f"{ROR_BASE_URI}{normalized_ror}"
    elif normalized_source == "pulse:infoscienceOrganizationIdentifier":
        normalized_infoscience_id = _normalize_infoscience_identifier(
            entity_id,
            entity_path="orgunit",
            legacy_path="organization",
        )
        if normalized_infoscience_id is not None:
            normalized_id = (
                f"{INFOSCIENCE_ORGANIZATION_BASE_URI}{normalized_infoscience_id}"
            )
    elif normalized_source == "pulse:githubOrganizationHandle":
        normalized_github_handle = _normalize_github_handle(entity_id)
        if normalized_github_handle is not None:
            normalized_id = f"{GITHUB_BASE_URI}{normalized_github_handle}"
    elif normalized_source == "pulse:githubRepositoryHandle":
        normalized_repository_handle = _normalize_repository_handle(entity_id)
        if normalized_repository_handle is not None:
            normalized_id = f"{GITHUB_BASE_URI}{normalized_repository_handle}"
    elif normalized_source == "schema:identifier":
        normalized_doi = _normalize_doi(entity_id)
        if normalized_doi is not None:
            normalized_id = f"{DOI_BASE_URI}{normalized_doi}"
    elif normalized_source == "pulse:infoscienceArticleIdentifier":
        normalized_infoscience_id = _normalize_infoscience_identifier(
            entity_id,
            entity_path="publication",
        )
        if normalized_infoscience_id is not None:
            normalized_id = f"{INFOSCIENCE_PUBLICATION_BASE_URI}{normalized_infoscience_id}"
    elif normalized_source == "uuid":
        normalized_id = _normalize_uuid(entity_id)

    if normalized_id is None:
        return None
    return normalized_id, normalized_source


def _normalize_orcid(orcid: str | None) -> str | None:
    if orcid is None:
        return None
    candidate = orcid
    if candidate.lower().startswith(ORCID_BASE_URI):
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    return _clean_text(candidate)


def _normalize_ror(ror: str | None) -> str | None:
    if ror is None:
        return None
    candidate = ror
    lower_candidate = candidate.lower()
    if lower_candidate.startswith(ROR_BASE_URI):
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    return _clean_text(candidate.lower())


def _normalize_github_handle(handle: str | None) -> str | None:
    if handle is None:
        return None
    candidate = handle.strip()
    lower_candidate = candidate.lower()
    if lower_candidate.startswith(GITHUB_BASE_URI):
        remainder = candidate[len(GITHUB_BASE_URI) :]
        candidate = remainder.split("/", maxsplit=1)[0]
    if candidate.startswith("@"):
        candidate = candidate[1:]
    return _clean_text(candidate)


def _normalize_infoscience_identifier(
    identifier: str | None,
    *,
    entity_path: str,
    legacy_path: str | None = None,
) -> str | None:
    candidate = _clean_text(identifier)
    if candidate is None:
        return None

    entity_pattern = (
        rf"^(?:https?://infoscience\.epfl\.ch)?/?(?:server/api/)?"
        rf"entities/{re.escape(entity_path)}/([^/?#]+)(?:/full)?(?:[/?#].*)?$"
    )
    entity_match = re.match(entity_pattern, candidate, flags=re.IGNORECASE)
    if entity_match:
        return _clean_text(entity_match.group(1))

    core_items_pattern = (
        r"^(?:https?://infoscience\.epfl\.ch)?/?(?:server/api/)?"
        r"core/items/([^/?#]+)(?:[/?#].*)?$"
    )
    core_items_match = re.match(core_items_pattern, candidate, flags=re.IGNORECASE)
    if core_items_match:
        return _clean_text(core_items_match.group(1))

    if legacy_path is not None:
        legacy_pattern = (
            rf"^(?:https?://infoscience\.epfl\.ch)?/?"
            rf"{re.escape(legacy_path)}/([^/?#]+)(?:[/?#].*)?$"
        )
        legacy_match = re.match(legacy_pattern, candidate, flags=re.IGNORECASE)
        if legacy_match:
            return _clean_text(legacy_match.group(1))

    return candidate


def _normalize_repository_handle(handle: str | None) -> str | None:
    if handle is None:
        return None

    candidate = handle.strip()
    lower_candidate = candidate.lower()
    if lower_candidate.startswith(GITHUB_BASE_URI):
        candidate = candidate[len(GITHUB_BASE_URI) :]

    handle_parts = [part for part in candidate.split("/") if part]
    if len(handle_parts) != REPOSITORY_HANDLE_PARTS:
        return None

    owner, repository = handle_parts
    if not owner or not repository:
        return None

    return f"{owner}/{repository}"


def _normalize_doi(doi: str | None) -> str | None:
    if doi is None:
        return None

    candidate = doi.strip()
    lower_candidate = candidate.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if lower_candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break

    if not candidate or "/" not in candidate:
        return None
    return candidate


def _deterministic_uuid(
    namespace: uuid.UUID,
    entity: dict[str, Any],
    keys_for_seed: tuple[str, ...],
) -> str:
    seed_parts: list[str] = []
    for key in keys_for_seed:
        value = _lookup_identifier(entity, (key,))
        if value is not None:
            seed_parts.append(value.lower())
    if not seed_parts:
        seed_parts.append(json.dumps(entity, sort_keys=True, separators=(",", ":")))
    seed = "|".join(seed_parts)
    return str(uuid.uuid5(namespace, seed))


def resolve_person_id(person: dict[str, Any]) -> tuple[str, str]:
    existing = _existing_resolution(person, PERSON_ID_SOURCES)
    if existing is not None:
        return existing

    orcid = _normalize_orcid(
        _lookup_identifier(
            person,
            ("pulse:orcid", "pulse:orcidIdentifier", "orcid", "orcidIdentifier"),
        ),
    )
    if orcid is not None:
        return f"{ORCID_BASE_URI}{orcid}", "pulse:orcid"

    infoscience_id = _normalize_infoscience_identifier(
        _lookup_identifier(
            person,
            (
                "pulse:infosciencePersonIdentifier",
                "infosciencePersonIdentifier",
            ),
        ),
        entity_path="person",
        legacy_path="person",
    )
    if infoscience_id is not None:
        return (
            f"{INFOSCIENCE_PERSON_BASE_URI}{infoscience_id}",
            "pulse:infosciencePersonIdentifier",
        )

    github_username = _normalize_github_handle(
        _lookup_identifier(
            person,
            ("pulse:githubUsername", "githubUsername"),
        ),
    )
    if github_username is not None:
        return f"{GITHUB_BASE_URI}{github_username}", "pulse:githubUsername"

    fallback_uuid = _deterministic_uuid(
        PERSON_UUID_NAMESPACE,
        person,
        ("schema:name", "name", "schema:email", "email"),
    )
    return fallback_uuid, "uuid"


def resolve_organization_id(organization: dict[str, Any]) -> tuple[str, str]:
    existing = _existing_resolution(organization, ORGANIZATION_ID_SOURCES)
    if existing is not None:
        return existing

    ror = _normalize_ror(
        _lookup_identifier(
            organization,
            ("pulse:ror", "ror", "schema:identifier"),
        ),
    )
    if ror is not None:
        return f"{ROR_BASE_URI}{ror}", "pulse:ror"

    infoscience_id = _normalize_infoscience_identifier(
        _lookup_identifier(
            organization,
            (
                "pulse:infoscienceOrganizationIdentifier",
                "infoscienceOrganizationIdentifier",
            ),
        ),
        entity_path="orgunit",
        legacy_path="organization",
    )
    if infoscience_id is not None:
        return (
            f"{INFOSCIENCE_ORGANIZATION_BASE_URI}{infoscience_id}",
            "pulse:infoscienceOrganizationIdentifier",
        )

    github_org_handle = _normalize_github_handle(
        _lookup_identifier(
            organization,
            ("pulse:githubOrganizationHandle", "githubOrganizationHandle"),
        ),
    )
    if github_org_handle is not None:
        return (
            f"{GITHUB_BASE_URI}{github_org_handle}",
            "pulse:githubOrganizationHandle",
        )

    fallback_uuid = _deterministic_uuid(
        ORGANIZATION_UUID_NAMESPACE,
        organization,
        ("schema:name", "name"),
    )
    return fallback_uuid, "uuid"


def resolve_repository_id(repository: dict[str, Any]) -> tuple[str, str]:
    existing = _existing_resolution(repository, REPOSITORY_ID_SOURCES)
    if existing is not None:
        return existing

    github_handle = _normalize_repository_handle(
        _lookup_identifier(
            repository,
            ("pulse:githubRepositoryHandle", "githubRepositoryHandle"),
        ),
    )
    if github_handle is not None:
        return (
            f"{GITHUB_BASE_URI}{github_handle}",
            "pulse:githubRepositoryHandle",
        )

    doi = _normalize_doi(
        _lookup_identifier(
            repository,
            ("schema:identifier", "doi"),
        ),
    )
    if doi is not None:
        return f"{DOI_BASE_URI}{doi}", "schema:identifier"

    fallback_uuid = _deterministic_uuid(
        REPOSITORY_UUID_NAMESPACE,
        repository,
        ("schema:name", "name", "pulse:githubRepositoryHandle", "schema:identifier"),
    )
    return fallback_uuid, "uuid"


def resolve_article_id(article: dict[str, Any]) -> tuple[str, str]:
    existing = _existing_resolution(article, ARTICLE_ID_SOURCES)
    if existing is not None:
        return existing

    doi = _normalize_doi(
        _lookup_identifier(
            article,
            ("schema:identifier", "doi"),
        ),
    )
    if doi is not None:
        return f"{DOI_BASE_URI}{doi}", "schema:identifier"

    infoscience_id = _normalize_infoscience_identifier(
        _lookup_identifier(
            article,
            (
                "pulse:infoscienceArticleIdentifier",
                "infoscienceArticleIdentifier",
            ),
        ),
        entity_path="publication",
    )
    if infoscience_id is not None:
        return (
            f"{INFOSCIENCE_PUBLICATION_BASE_URI}{infoscience_id}",
            "pulse:infoscienceArticleIdentifier",
        )

    fallback_uuid = _deterministic_uuid(
        ARTICLE_UUID_NAMESPACE,
        article,
        ("schema:name", "name", "schema:datePublished", "schema:identifier"),
    )
    return fallback_uuid, "uuid"
