from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid5

from src.v2.agents.models import AgentResult, ProviderSet, validate_permissive

MEMBERSHIP_UUID_NAMESPACE = UUID("4e635472-2945-5894-bd8f-b70918ed36d1")


def _as_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


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


def _collect_known_organizations(context: dict[str, Any]) -> list[dict[str, Any]]:
    organizations: list[dict[str, Any]] = []
    for key in ("known_organizations", "organizations"):
        value = context.get(key)
        if isinstance(value, list):
            organizations.extend(item for item in value if isinstance(item, dict) and item)
    organizations.extend(_collect_pipeline_entities(context, prefix="org_agent"))
    return organizations


def _register_lookup_token(lookup: dict[str, str], token: Any, canonical_id: str) -> None:
    normalized = _normalize_token(token)
    if normalized is None:
        return
    lookup.setdefault(normalized, canonical_id)


def _build_organization_lookup(organizations: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for organization in organizations:
        org_id = _as_string(organization.get("id"))
        if org_id is None:
            continue
        _register_lookup_token(lookup, org_id, org_id)
        _register_lookup_token(lookup, organization.get("schema:name"), org_id)
        _register_lookup_token(lookup, organization.get("pulse:githubOrganizationHandle"), org_id)
        _register_lookup_token(lookup, organization.get("schema:identifier"), org_id)

        identifiers = organization.get("identifiers")
        if isinstance(identifiers, dict):
            _register_lookup_token(
                lookup,
                identifiers.get("pulse:githubOrganizationHandle"),
                org_id,
            )
            _register_lookup_token(lookup, identifiers.get("pulse:ror"), org_id)
    return lookup


def _extract_affiliation_signals(  # noqa: C901
    person: dict[str, Any],
) -> list[dict[str, str | None]]:
    signals: list[dict[str, str | None]] = []

    def _append_signal(entry: Any) -> None:
        if isinstance(entry, str):
            organization_name = _as_string(entry)
            if organization_name:
                signals.append(
                    {
                        "organization": organization_name,
                        "role": None,
                        "start_date": None,
                        "end_date": None,
                    },
                )
            return

        if not isinstance(entry, dict):
            return

        organization_name = _as_string(
            entry.get("organization")
            or entry.get("organizationId")
            or entry.get("name")
            or entry.get("schema:name")
            or entry.get("org:organization"),
        )
        if organization_name is None:
            return

        signals.append(
            {
                "organization": organization_name,
                "role": _as_string(entry.get("role") or entry.get("org:role")),
                "start_date": _as_string(
                    entry.get("start_date") or entry.get("time:hasBeginning"),
                ),
                "end_date": _as_string(
                    entry.get("end_date") or entry.get("time:hasEnd"),
                ),
            },
        )

    for key in ("affiliations", "orcid_affiliations"):
        value = person.get(key)
        if isinstance(value, list):
            for entry in value:
                _append_signal(entry)

    orcid_record = person.get("orcid_record")
    if isinstance(orcid_record, dict):
        for key in ("employment", "education"):
            value = orcid_record.get(key)
            if isinstance(value, list):
                for entry in value:
                    _append_signal(entry)

    return signals


def _deterministic_membership_uuid(composite_id: str) -> str:
    return str(uuid5(MEMBERSHIP_UUID_NAMESPACE, composite_id))


def _build_membership_payload(
    *,
    composite_id: str,
    organization_id: str,
    role: str | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    return {
        "id": composite_id,
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {
            "pulse:composite": composite_id,
            "uuid": _deterministic_membership_uuid(composite_id),
        },
        "idSource": "pulse:composite",
        "org:organization": organization_id,
        "org:role": role,
        "time:hasBeginning": start_date,
        "time:hasEnd": end_date,
    }


class MembershipAgentV2:
    """Deterministic membership agent from person affiliation signals."""

    async def run(  # noqa: C901, PLR0912
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del providers

        warnings: list[str] = []
        persons = _collect_known_persons(context)
        organization_lookup = _build_organization_lookup(_collect_known_organizations(context))

        membership_data_by_composite: dict[str, dict[str, str | None]] = {}
        for person in persons:
            person_id = _as_string(person.get("id"))
            if person_id is None:
                continue

            for signal in _extract_affiliation_signals(person):
                organization_name = signal.get("organization")
                normalized_organization = _normalize_token(organization_name)
                if normalized_organization is None:
                    continue

                organization_id = organization_lookup.get(normalized_organization)
                if organization_id is None:
                    _append_unique(
                        warnings,
                        (
                            "Unresolved membership organization mapping: "
                            f"person={person_id}, organization={organization_name}"
                        ),
                    )
                    continue

                composite_id = f"{person_id}_{organization_id}"
                existing = membership_data_by_composite.setdefault(
                    composite_id,
                    {
                        "organization_id": organization_id,
                        "role": None,
                        "start_date": None,
                        "end_date": None,
                    },
                )
                if existing["role"] is None and _as_string(signal.get("role")):
                    existing["role"] = _as_string(signal.get("role"))
                if existing["start_date"] is None and _as_string(signal.get("start_date")):
                    existing["start_date"] = _as_string(signal.get("start_date"))
                if existing["end_date"] is None and _as_string(signal.get("end_date")):
                    existing["end_date"] = _as_string(signal.get("end_date"))

        if not membership_data_by_composite:
            _append_unique(warnings, "No membership candidates were derived from context")
            return AgentResult(
                data={},
                warnings=warnings,
                raw_output={},
                stats={"memberships": []},
            )

        validated_memberships: list[dict[str, Any]] = []
        raw_memberships: list[dict[str, Any]] = []

        for composite_id in sorted(membership_data_by_composite):
            membership_data = membership_data_by_composite[composite_id]
            payload = _build_membership_payload(
                composite_id=composite_id,
                organization_id=str(membership_data["organization_id"]),
                role=_as_string(membership_data["role"]),
                start_date=_as_string(membership_data["start_date"]),
                end_date=_as_string(membership_data["end_date"]),
            )
            raw_payload = deepcopy(payload)
            validated_payload, validation_warnings = validate_permissive(
                payload,
                schema_name="membership",
            )
            for warning in validation_warnings:
                _append_unique(
                    warnings,
                    f"{validated_payload.get('id', 'membership')}: {warning}",
                )
            validated_memberships.append(validated_payload)
            raw_memberships.append(raw_payload)

        primary_membership = validated_memberships[0]
        primary_raw_output = raw_memberships[0]
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            primary_membership = {**primary_membership, **overrides}
            primary_raw_output = deepcopy(primary_membership)
            primary_membership, override_warnings = validate_permissive(
                primary_membership,
                schema_name="membership",
            )
            for warning in override_warnings:
                _append_unique(
                    warnings,
                    f"{primary_membership.get('id', 'membership')}: {warning}",
                )
            validated_memberships[0] = primary_membership

        return AgentResult(
            data=primary_membership,
            warnings=warnings,
            raw_output=primary_raw_output,
            stats={
                "memberships": deepcopy(validated_memberships),
                "membership_count": len(validated_memberships),
            },
        )
