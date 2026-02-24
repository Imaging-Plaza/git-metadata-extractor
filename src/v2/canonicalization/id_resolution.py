from __future__ import annotations

import json
import uuid
from typing import Any

INFOSCIENCE_PERSON_BASE_URI = "https://infoscience.epfl.ch/person/"
INFOSCIENCE_ORGANIZATION_BASE_URI = "https://infoscience.epfl.ch/organization/"
GITHUB_BASE_URI = "https://github.com/"
ORCID_BASE_URI = "https://orcid.org/"
ROR_BASE_URI = "https://ror.org/"
DOI_BASE_URI = "https://doi.org/"

PERSON_UUID_NAMESPACE = uuid.UUID("bfc0a4f9-2ef5-59eb-9bf8-76ef425de91e")
ORGANIZATION_UUID_NAMESPACE = uuid.UUID("4f7f847a-8d6e-56b3-9164-2668e51f040f")
REPOSITORY_UUID_NAMESPACE = uuid.UUID("c5b462e3-9cf5-5b59-b2df-bdbfd9bd0ac5")

PERSON_ID_SOURCES = {"orcid", "infosciencePersonIdentifier", "githubUsername", "uuid"}
ORGANIZATION_ID_SOURCES = {
    "ror",
    "infoscienceOrganizationIdentifier",
    "githubOrganizationHandle",
    "uuid",
}
REPOSITORY_ID_SOURCES = {"githubRepositoryHandle", "doi", "uuid"}
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


def _existing_resolution(
    entity: dict[str, Any],
    valid_sources: set[str],
) -> tuple[str, str] | None:
    entity_id = _clean_text(entity.get("id"))
    id_source = _clean_text(entity.get("idSource"))
    if entity_id is None or id_source is None:
        return None
    if id_source not in valid_sources:
        return None
    return entity_id, id_source


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
        return f"{ORCID_BASE_URI}{orcid}", "orcid"

    infoscience_id = _lookup_identifier(
        person,
        (
            "pulse:infosciencePersonIdentifier",
            "infosciencePersonIdentifier",
        ),
    )
    if infoscience_id is not None:
        return f"{INFOSCIENCE_PERSON_BASE_URI}{infoscience_id}", "infosciencePersonIdentifier"

    github_username = _normalize_github_handle(
        _lookup_identifier(
            person,
            ("pulse:githubUsername", "githubUsername"),
        ),
    )
    if github_username is not None:
        return f"{GITHUB_BASE_URI}{github_username}", "githubUsername"

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
        return f"{ROR_BASE_URI}{ror}", "ror"

    infoscience_id = _lookup_identifier(
        organization,
        (
            "pulse:infoscienceOrganizationIdentifier",
            "infoscienceOrganizationIdentifier",
        ),
    )
    if infoscience_id is not None:
        return (
            f"{INFOSCIENCE_ORGANIZATION_BASE_URI}{infoscience_id}",
            "infoscienceOrganizationIdentifier",
        )

    github_org_handle = _normalize_github_handle(
        _lookup_identifier(
            organization,
            ("pulse:githubOrganizationHandle", "githubOrganizationHandle"),
        ),
    )
    if github_org_handle is not None:
        return f"{GITHUB_BASE_URI}{github_org_handle}", "githubOrganizationHandle"

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
        return f"{GITHUB_BASE_URI}{github_handle}", "githubRepositoryHandle"

    doi = _normalize_doi(
        _lookup_identifier(
            repository,
            ("schema:identifier", "doi"),
        ),
    )
    if doi is not None:
        return f"{DOI_BASE_URI}{doi}", "doi"

    fallback_uuid = _deterministic_uuid(
        REPOSITORY_UUID_NAMESPACE,
        repository,
        ("schema:name", "name", "pulse:githubRepositoryHandle", "schema:identifier"),
    )
    return fallback_uuid, "uuid"
