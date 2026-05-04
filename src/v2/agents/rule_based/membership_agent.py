from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from typing import Any

from src.v2.agents.models import (
    AgentResult,
    ProviderSet,
    generate_uuid,
    validate_permissive,
)
from src.v2.canonicalization.string_utils import normalize_string, strip_accents

LOOKUP_SPLIT_PATTERN = re.compile(r"\s*[-–—|/&]\s*|;|,")
PARENTHETICAL_PATTERN = re.compile(r"\s*\([^)]*\)")
NON_ALNUM_SPLIT_PATTERN = re.compile(r"[^a-z0-9]+")
COMMON_ACRONYM_STOPWORDS = {
    "a",
    "an",
    "and",
    "de",
    "des",
    "di",
    "du",
    "et",
    "for",
    "in",
    "la",
    "le",
    "of",
    "on",
    "the",
    "und",
    "university",
}


def _as_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize_token(value: Any) -> str | None:
    candidate = _as_string(value)
    return candidate.casefold() if candidate else None


def _lookup_token_variants(token: Any) -> list[str]:
    candidate = _as_string(token)
    if candidate is None:
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def _add(value: str | None) -> None:
        if value is None:
            return
        normalized = value.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        variants.append(normalized)

    def _add_normalized_forms(value: str) -> None:
        lowered = value.casefold()
        _add(lowered)
        _add(strip_accents(lowered))
        collapsed = normalize_string(value)
        _add(collapsed)
        _add(collapsed.replace(" ", ""))
        tokens = [token for token in collapsed.split() if token]
        if "university" in tokens and tokens[0] != "university":
            _add(f"{tokens[0]} university")
        if "institute" in tokens and tokens[0] != "institute":
            _add(f"{tokens[0]} institute")

    raw_candidates: list[str] = []
    raw_seen: set[str] = set()

    def _add_raw(value: str | None) -> None:
        normalized = _as_string(value)
        if normalized is None or normalized in raw_seen:
            return
        raw_seen.add(normalized)
        raw_candidates.append(normalized)

    _add_raw(candidate)
    if candidate.startswith("@"):
        _add_raw(candidate[1:])

    for raw_value in list(raw_candidates):
        _add_raw(PARENTHETICAL_PATTERN.sub("", raw_value))

    for raw_value in list(raw_candidates):
        for segment in LOOKUP_SPLIT_PATTERN.split(raw_value):
            if len(segment.strip()) >= 3:
                _add_raw(segment)

    for raw_value in raw_candidates:
        _add_normalized_forms(raw_value)

    return variants


def _organization_acronym_tokens(value: Any) -> list[str]:
    candidate = _as_string(value)
    if candidate is None:
        return []

    normalized = normalize_string(candidate)
    parts = [
        part
        for part in NON_ALNUM_SPLIT_PATTERN.split(normalized)
        if part
    ]
    if not parts:
        return []

    acronym_chars = [part[0] for part in parts if part not in COMMON_ACRONYM_STOPWORDS]
    if len(acronym_chars) < 2:
        acronym_chars = [part[0] for part in parts]
    acronym = "".join(acronym_chars)
    if len(acronym) < 2:
        return []
    return [acronym]


def _organization_partial_name_tokens(value: Any) -> list[str]:
    candidate = _as_string(value)
    if candidate is None:
        return []

    tokens = [token for token in normalize_string(candidate).split() if token]
    if len(tokens) < 2:
        return []

    partials: list[str] = []
    if "university" in tokens and tokens[0] != "university":
        partials.append(f"{tokens[0]} university")
    if "institute" in tokens and tokens[0] != "institute":
        partials.append(f"{tokens[0]} institute")
    return partials


def _resolve_lookup_token(lookup: dict[str, str], token: Any) -> str | None:
    for variant in _lookup_token_variants(token):
        resolved = lookup.get(variant)
        if isinstance(resolved, str):
            return resolved
    return None


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
    for normalized in _lookup_token_variants(token):
        lookup.setdefault(normalized, canonical_id)


def _register_organization_name_lookup_tokens(
    lookup: dict[str, str],
    token: Any,
    canonical_id: str,
) -> None:
    name = _as_string(token)
    if name is None:
        return

    _register_lookup_token(lookup, name, canonical_id)
    _register_lookup_token(lookup, name.replace("centre", "center"), canonical_id)
    _register_lookup_token(lookup, name.replace("center", "centre"), canonical_id)
    for acronym in _organization_acronym_tokens(name):
        _register_lookup_token(lookup, acronym, canonical_id)
    for partial_name in _organization_partial_name_tokens(name):
        _register_lookup_token(lookup, partial_name, canonical_id)


def _register_organization_handle_lookup_tokens(
    lookup: dict[str, str],
    token: Any,
    canonical_id: str,
) -> None:
    handle = _as_string(token)
    if handle is None:
        return
    normalized_handle = handle[1:] if handle.startswith("@") else handle
    if not normalized_handle:
        return
    _register_lookup_token(lookup, normalized_handle, canonical_id)
    _register_lookup_token(lookup, f"@{normalized_handle}", canonical_id)
    _register_lookup_token(lookup, handle, canonical_id)


def _build_organization_lookup(organizations: list[dict[str, Any]]) -> dict[str, str]:  # noqa: C901
    lookup: dict[str, str] = {}
    for organization in organizations:
        org_id = _as_string(organization.get("id"))
        if org_id is None:
            continue
        _register_lookup_token(lookup, org_id, org_id)
        _register_organization_name_lookup_tokens(
            lookup,
            organization.get("schema:name"),
            org_id,
        )
        _register_organization_handle_lookup_tokens(
            lookup,
            organization.get("pulse:githubOrganizationHandle"),
            org_id,
        )
        _register_lookup_token(lookup, organization.get("schema:identifier"), org_id)
        for key in ("aliases", "acronyms"):
            values = organization.get(key)
            if isinstance(values, list):
                for value in values:
                    _register_organization_name_lookup_tokens(lookup, value, org_id)
        labels = organization.get("labels")
        if isinstance(labels, list):
            for label_payload in labels:
                label = label_payload
                if isinstance(label_payload, dict):
                    label = label_payload.get("label")
                _register_organization_name_lookup_tokens(lookup, label, org_id)

        identifiers = organization.get("identifiers")
        if isinstance(identifiers, dict):
            _register_organization_handle_lookup_tokens(
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


def _build_membership_payload(
    *,
    composite_id: str,
    organization_id: str,
    role: str | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    # Defend against inverted dates from upstream sources (ORCID returns
    # employment entries with `start_date` and `end_date` reversed in some
    # records). The SHACL shape requires `time:hasBeginning <= time:hasEnd`.
    if isinstance(start_date, str) and isinstance(end_date, str):
        try:
            if date.fromisoformat(start_date[:10]) > date.fromisoformat(end_date[:10]):
                start_date, end_date = end_date, start_date
        except ValueError:
            pass
    return {
        "id": composite_id,
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {
            "pulse:composite": composite_id,
            "uuid": generate_uuid(),
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

                organization_id = _resolve_lookup_token(organization_lookup, organization_name)
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
