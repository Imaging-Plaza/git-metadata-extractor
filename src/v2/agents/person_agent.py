from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

from src.v2.agents.models import (
    AgentResult,
    ProviderSet,
    generate_uuid,
    validate_permissive,
)
from src.v2.providers.base import ProviderNotFoundError

HASH_LENGTH = 12
HASHED_LOCAL_PART_PATTERN = re.compile(r"^[0-9a-f]{12}$|^[0-9a-f]{64}$", re.IGNORECASE)


def _normalize_orcid(orcid_value: Any) -> str | None:
    if not isinstance(orcid_value, str):
        return None
    candidate = orcid_value.strip()
    if candidate.lower().startswith("https://orcid.org/"):
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    return candidate or None


def _anonymize_email(email: Any) -> str | None:
    if not isinstance(email, str) or "@" not in email:
        return None
    local_part, domain = email.split("@", maxsplit=1)
    if not local_part or not domain:
        return None
    if HASHED_LOCAL_PART_PATTERN.fullmatch(local_part):
        return email
    hashed_local = hashlib.sha256(local_part.encode("utf-8")).hexdigest()[:HASH_LENGTH]
    return f"{hashed_local}@{domain}"


def _pick_best_infoscience_match(results: Any) -> dict[str, Any] | None:
    if not isinstance(results, list):
        return None
    candidates = [item for item in results if isinstance(item, dict)]
    if not candidates:
        return None

    def _score(item: dict[str, Any]) -> float:
        score = item.get("score")
        return float(score) if isinstance(score, (int, float)) else -1.0

    return sorted(candidates, key=_score, reverse=True)[0]


def _deduplicate_preserve_order(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduplicated.append(value)
        seen.add(value)
    return deduplicated


def _normalize_affiliation_entries(entries: Any) -> list[dict[str, str | None]]:
    if not isinstance(entries, list):
        return []

    normalized_entries: list[dict[str, str | None]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        organization = entry.get("organization")
        if not isinstance(organization, str) or not organization:
            continue
        normalized_entries.append(
            {
                "organization": organization,
                "department": (
                    entry.get("department")
                    if isinstance(entry.get("department"), str)
                    else None
                ),
                "role": entry.get("role") if isinstance(entry.get("role"), str) else None,
                "start_date": (
                    entry.get("start_date")
                    if isinstance(entry.get("start_date"), str)
                    else None
                ),
                "end_date": (
                    entry.get("end_date")
                    if isinstance(entry.get("end_date"), str)
                    else None
                ),
            },
        )
    return normalized_entries


def _resolve_username(context: dict[str, Any]) -> str:
    for key in ("username", "github_username"):
        value = context.get(key)
        if isinstance(value, str) and value:
            return value
    message = "Person context is missing a GitHub username"
    raise ValueError(message)


class PersonAgentV2:
    """Person agent wrapper with permissive output validation."""

    async def run(  # noqa: C901, PLR0912, PLR0915
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        warnings: list[str] = []
        username = _resolve_username(context)
        github_user = providers.github.get_user(username)

        orcid_record: Any | None = None
        orcid_identifier_hint = _normalize_orcid(context.get("orcid")) or _normalize_orcid(
            github_user.get("orcid"),
        )

        if providers.orcid and orcid_identifier_hint:
            try:
                orcid_record = providers.orcid.get_person_by_orcid(orcid_identifier_hint)
            except (ProviderNotFoundError, ValueError) as exc:
                warnings.append(f"ORCID lookup failed: {exc}")
        elif not providers.orcid:
            warnings.append("ORCID provider not configured for person enrichment")

        infoscience_match: dict[str, Any] | None = None
        if providers.infoscience:
            search_query = (
                context.get("person_query")
                or github_user.get("name")
                or username
            )
            infoscience_results = providers.infoscience.search_person(str(search_query))
            infoscience_match = _pick_best_infoscience_match(infoscience_results)
        else:
            warnings.append("Infoscience provider not configured for person enrichment")

        normalized_orcid = _normalize_orcid(
            (orcid_record or {}).get("orcid_id") if orcid_record else None,
        ) or _normalize_orcid((infoscience_match or {}).get("orcid"))
        if not normalized_orcid and not providers.orcid:
            normalized_orcid = orcid_identifier_hint
        infoscience_id = (
            (infoscience_match or {}).get("infosciencePersonIdentifier")
            if infoscience_match
            else None
        )
        github_username = github_user.get("login")
        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        identifier_hierarchy: list[tuple[str, str | None]] = [
            ("pulse:orcid", normalized_orcid),
            ("pulse:infosciencePersonIdentifier", infoscience_id if isinstance(infoscience_id, str) else None),
            ("pulse:githubUsername", github_username if isinstance(github_username, str) else None),
            ("uuid", uuid_value),
        ]
        id_source, resolved_id = next(
            (identifier, value)
            for identifier, value in identifier_hierarchy
            if value
        )

        affiliations: list[str] = []
        if orcid_record and isinstance(orcid_record.get("affiliations"), list):
            affiliations.extend(
                [item for item in orcid_record["affiliations"] if isinstance(item, str)],
            )
        if infoscience_match and isinstance(infoscience_match.get("affiliations"), list):
            affiliations.extend(
                [item for item in infoscience_match["affiliations"] if isinstance(item, str)],
            )
        if isinstance(github_user.get("company"), str) and github_user["company"]:
            affiliations.append(github_user["company"])
        affiliations = _deduplicate_preserve_order(affiliations)

        membership_ids = [f"{resolved_id}_{affiliation}" for affiliation in affiliations]
        repository_ownership = []
        source_repositories = context.get("source_repositories")
        if isinstance(source_repositories, list):
            repository_ownership = [
                repository
                for repository in source_repositories
                if isinstance(repository, str) and repository
            ]
        repositories = github_user.get("repositories")
        if not repository_ownership and isinstance(repositories, list) and isinstance(github_username, str):
            repository_ownership = [
                f"{github_username}/{repo_name}"
                for repo_name in repositories
                if isinstance(repo_name, str) and repo_name
            ]
        email = _anonymize_email(
            context.get("email") or github_user.get("email"),
        )

        payload = {
            "id": resolved_id,
            "type": "schema:Person",
            "shacl": "pulse:PersonShape",
            "identifiers": {
                "pulse:orcid": normalized_orcid,
                "pulse:infosciencePersonIdentifier": infoscience_id,
                "pulse:githubUsername": github_username,
                "uuid": uuid_value,
            },
            "idSource": id_source,
            "schema:name": (
                (orcid_record or {}).get("name")
                or (infoscience_match or {}).get("name")
                or github_user.get("name")
                or username
            ),
            "schema:url": (
                (infoscience_match or {}).get("profileUrl")
                if infoscience_match
                else github_user.get("html_url")
            ),
            "pulse:githubUsername": github_username,
            "pulse:orcidIdentifier": normalized_orcid,
            "pulse:infosciencePersonIdentifier": infoscience_id,
            "org:hasMembership": membership_ids,
            "pulse:hasContribution": _deduplicate_preserve_order(
                [
                    contribution
                    for contribution in context.get("contributions", [])
                    if isinstance(contribution, str)
                ] if isinstance(context.get("contributions"), list) else [],
            ),
            "pulse:owns": repository_ownership,
        }
        if email is not None:
            payload["schema:email"] = email

        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        raw_output = deepcopy(payload)
        validated_payload, validation_warnings = validate_permissive(
            payload,
            schema_name="person",
        )
        warnings.extend(validation_warnings)

        derivation_stats = {
            "person_id": validated_payload.get("id"),
            "github_username": github_username,
            "source_repositories": deepcopy(repository_ownership),
            "affiliation_names": deepcopy(affiliations),
            "orcid_affiliations": _normalize_affiliation_entries(
                (orcid_record or {}).get("employment") if isinstance(orcid_record, dict) else [],
            )
            + _normalize_affiliation_entries(
                (orcid_record or {}).get("education") if isinstance(orcid_record, dict) else [],
            ),
            "infoscience_affiliations": deepcopy(
                [
                    affiliation
                    for affiliation in (infoscience_match or {}).get("affiliations", [])
                    if isinstance(affiliation, str) and affiliation
                ],
            )
            if isinstance(infoscience_match, dict)
            else [],
            "membership_ids": deepcopy(
                [
                    membership
                    for membership in validated_payload.get("org:hasMembership", [])
                    if isinstance(membership, str)
                ],
            ),
            "contribution_ids": deepcopy(
                [
                    contribution
                    for contribution in validated_payload.get("pulse:hasContribution", [])
                    if isinstance(contribution, str)
                ],
            ),
        }

        return AgentResult(
            data=validated_payload,
            warnings=warnings,
            raw_output=raw_output,
            stats={"derivation": derivation_stats},
        )
