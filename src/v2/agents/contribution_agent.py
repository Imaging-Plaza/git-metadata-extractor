from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.v2.agents.models import (
    AgentResult,
    ProviderSet,
    generate_uuid,
    validate_permissive,
)


def _as_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_non_negative_int(value: Any) -> int | None:
    if not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _normalize_token(value: Any) -> str | None:
    candidate = _as_string(value)
    return candidate.casefold() if candidate else None


def _append_unique(target: list[str], warning: str) -> None:
    if warning and warning not in target:
        target.append(warning)


def _is_pipeline_key(candidate: str, prefix: str) -> bool:
    normalized = candidate.strip().lower()
    return normalized == prefix or normalized.startswith(f"{prefix}:")


def _collect_pipeline_entities(
    context: dict[str, Any],
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    pipeline_outputs = context.get("pipeline_outputs")
    if not isinstance(pipeline_outputs, dict):
        return []

    entities: list[dict[str, Any]] = []
    for key, value in pipeline_outputs.items():
        if not isinstance(key, str) or not _is_pipeline_key(key, prefix):
            continue
        if isinstance(value, dict) and value:
            entities.append(value)
    return entities


def _collect_known_persons(context: dict[str, Any]) -> list[dict[str, Any]]:
    persons: list[dict[str, Any]] = []
    for key in ("known_persons", "persons"):
        value = context.get(key)
        if isinstance(value, list):
            persons.extend(item for item in value if isinstance(item, dict) and item)
    persons.extend(_collect_pipeline_entities(context, prefix="person_agent"))
    return persons


def _collect_known_repositories(context: dict[str, Any]) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    for key in ("known_repositories", "repositories"):
        value = context.get(key)
        if isinstance(value, list):
            repositories.extend(item for item in value if isinstance(item, dict) and item)

    repository_context = context.get("repository_context")
    if isinstance(repository_context, dict):
        repositories.append(
            {
                "id": _as_string(repository_context.get("full_name")),
                "pulse:githubRepositoryHandle": _as_string(repository_context.get("full_name")),
                "contributors": repository_context.get("contributors"),
            },
        )

    repositories.extend(_collect_pipeline_entities(context, prefix="repo_agent"))
    return repositories


def _register_lookup_token(lookup: dict[str, str], token: Any, canonical_id: str) -> None:
    normalized = _normalize_token(token)
    if normalized is None:
        return
    lookup.setdefault(normalized, canonical_id)


def _build_organization_lookup(organizations: list[dict[str, Any]]) -> set[str]:
    lookup: set[str] = set()

    def _register(token: Any) -> None:
        normalized = _normalize_token(token)
        if normalized is not None:
            lookup.add(normalized)

    for organization in organizations:
        _register(organization.get("id"))
        _register(organization.get("schema:name"))
        _register(organization.get("pulse:githubOrganizationHandle"))
        identifiers = organization.get("identifiers")
        if isinstance(identifiers, dict):
            _register(identifiers.get("pulse:githubOrganizationHandle"))
    return lookup


def _collect_known_organizations(context: dict[str, Any]) -> list[dict[str, Any]]:
    organizations: list[dict[str, Any]] = []
    for key in ("known_organizations", "organizations"):
        value = context.get(key)
        if isinstance(value, list):
            organizations.extend(item for item in value if isinstance(item, dict) and item)
    organizations.extend(_collect_pipeline_entities(context, prefix="org_agent"))
    return organizations


def _build_person_lookup(persons: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for person in persons:
        person_id = _as_string(person.get("id"))
        if person_id is None:
            continue

        _register_lookup_token(lookup, person_id, person_id)
        _register_lookup_token(lookup, person.get("schema:name"), person_id)
        _register_lookup_token(lookup, person.get("pulse:githubUsername"), person_id)

        identifiers = person.get("identifiers")
        if isinstance(identifiers, dict):
            _register_lookup_token(
                lookup,
                identifiers.get("pulse:githubUsername"),
                person_id,
            )
    return lookup


def _extract_contributor_signals(repository: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    contributors = repository.get("contributors")
    if not isinstance(contributors, list):
        return signals

    for contributor in contributors:
        if isinstance(contributor, str):
            login = _as_string(contributor)
            if login:
                signals.append(
                    {
                        "login": login,
                        "count": None,
                        "first_date": None,
                        "last_date": None,
                    },
                )
            continue

        if not isinstance(contributor, dict):
            continue

        contributor_type = contributor.get("type")
        if isinstance(contributor_type, str) and contributor_type.lower() == "organization":
            continue

        login = _as_string(contributor.get("login") or contributor.get("username"))
        if login is None:
            continue

        signals.append(
            {
                "login": login,
                "count": _as_non_negative_int(
                    contributor.get("contributions")
                    if contributor.get("contributions") is not None
                    else contributor.get("pulse:contributionCount"),
                ),
                "first_date": _as_string(
                    contributor.get("firstContributionDate")
                    or contributor.get("pulse:firstContributionDate")
                    or contributor.get("first_contribution_date"),
                ),
                "last_date": _as_string(
                    contributor.get("lastContributionDate")
                    or contributor.get("pulse:lastContributionDate")
                    or contributor.get("last_contribution_date"),
                ),
            },
        )
    return signals


def _earliest_date(current: str | None, candidate: str | None) -> str | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return min(current, candidate)


def _latest_date(current: str | None, candidate: str | None) -> str | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return max(current, candidate)


def _build_contribution_payload(  # noqa: PLR0913
    *,
    composite_id: str,
    person_id: str,
    repository_id: str,
    contribution_count: int,
    first_contribution_date: str | None,
    last_contribution_date: str | None,
) -> dict[str, Any]:
    return {
        "id": composite_id,
        "type": "pulse:Contribution",
        "shacl": "pulse:ContributionShape",
        "identifiers": {
            "pulse:composite": composite_id,
            "uuid": generate_uuid(),
        },
        "idSource": "pulse:composite",
        "pulse:contributionTo": repository_id,
        "pulse:contributionCount": contribution_count,
        "pulse:firstContributionDate": first_contribution_date,
        "pulse:lastContributionDate": last_contribution_date,
        "schema:author": person_id,
    }


class ContributionAgentV2:
    """Deterministic contribution agent from repository contributor metadata."""

    async def run(  # noqa: C901
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del providers

        warnings: list[str] = []
        person_lookup = _build_person_lookup(_collect_known_persons(context))
        organization_lookup = _build_organization_lookup(_collect_known_organizations(context))
        repositories = _collect_known_repositories(context)

        contribution_data_by_composite: dict[str, dict[str, Any]] = {}
        for repository in repositories:
            repository_id = _as_string(
                repository.get("id")
                or repository.get("pulse:githubRepositoryHandle")
                or repository.get("full_name"),
            )
            if repository_id is None:
                continue

            for signal in _extract_contributor_signals(repository):
                contributor_login = signal.get("login")
                normalized_login = _normalize_token(contributor_login)
                if normalized_login is None:
                    continue

                person_id = person_lookup.get(normalized_login)
                if person_id is None:
                    if normalized_login in organization_lookup:
                        continue
                    _append_unique(
                        warnings,
                        (
                            "Unresolved contribution person mapping: "
                            f"repository={repository_id}, contributor={contributor_login}"
                        ),
                    )
                    continue

                composite_id = f"{person_id}_{repository_id}"
                existing = contribution_data_by_composite.setdefault(
                    composite_id,
                    {
                        "person_id": person_id,
                        "repository_id": repository_id,
                        "count": 0,
                        "first_date": None,
                        "last_date": None,
                    },
                )
                if isinstance(signal.get("count"), int):
                    existing["count"] = max(int(existing["count"]), int(signal["count"]))
                existing["first_date"] = _earliest_date(
                    _as_string(existing.get("first_date")),
                    _as_string(signal.get("first_date")),
                )
                existing["last_date"] = _latest_date(
                    _as_string(existing.get("last_date")),
                    _as_string(signal.get("last_date")),
                )

        if not contribution_data_by_composite:
            return AgentResult(
                data={},
                warnings=warnings,
                raw_output={},
                stats={"contributions": []},
            )

        validated_contributions: list[dict[str, Any]] = []
        raw_contributions: list[dict[str, Any]] = []
        for composite_id in sorted(contribution_data_by_composite):
            contribution_data = contribution_data_by_composite[composite_id]
            payload = _build_contribution_payload(
                composite_id=composite_id,
                person_id=str(contribution_data["person_id"]),
                repository_id=str(contribution_data["repository_id"]),
                contribution_count=int(contribution_data["count"]),
                first_contribution_date=_as_string(contribution_data.get("first_date")),
                last_contribution_date=_as_string(contribution_data.get("last_date")),
            )
            raw_payload = deepcopy(payload)
            validated_payload, validation_warnings = validate_permissive(
                payload,
                schema_name="contribution",
            )
            for warning in validation_warnings:
                _append_unique(
                    warnings,
                    f"{validated_payload.get('id', 'contribution')}: {warning}",
                )
            validated_contributions.append(validated_payload)
            raw_contributions.append(raw_payload)

        primary_contribution = validated_contributions[0]
        primary_raw_output = raw_contributions[0]
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            primary_contribution = {**primary_contribution, **overrides}
            primary_raw_output = deepcopy(primary_contribution)
            primary_contribution, override_warnings = validate_permissive(
                primary_contribution,
                schema_name="contribution",
            )
            for warning in override_warnings:
                _append_unique(
                    warnings,
                    f"{primary_contribution.get('id', 'contribution')}: {warning}",
                )
            validated_contributions[0] = primary_contribution

        return AgentResult(
            data=primary_contribution,
            warnings=warnings,
            raw_output=primary_raw_output,
            stats={
                "contributions": deepcopy(validated_contributions),
                "contribution_count": len(validated_contributions),
            },
        )
