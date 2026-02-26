from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.v2.agents.models import (
    AgentResult,
    ProviderSet,
    generate_uuid,
    validate_permissive,
)
from src.v2.providers.base import ProviderNotFoundError


def _resolve_org_name(context: dict[str, Any]) -> str:
    for key in ("org_name", "organization", "github_organization_handle"):
        value = context.get(key)
        if isinstance(value, str) and value:
            return value
    message = "Organization context is missing a GitHub organization handle"
    raise ValueError(message)


def _pick_best_orgunit_match(results: Any) -> dict[str, Any] | None:
    if not isinstance(results, list):
        return None
    candidates = [item for item in results if isinstance(item, dict)]
    if not candidates:
        return None
    return candidates[0]


def _classify_organization_type(ror_types: list[str]) -> str:
    normalized = [value.lower() for value in ror_types]
    if any("education" in value for value in normalized):
        return "pulse:University"
    if any("government" in value for value in normalized):
        return "pulse:GovernmentAgency"
    if any("nonprofit" in value or "non-profit" in value for value in normalized):
        return "pulse:NonProfitOrganization"
    if any("company" in value for value in normalized):
        return "pulse:PrivateCompany"
    if any("research" in value for value in normalized):
        return "pulse:ResearchInstitution"
    return "pulse:OtherOrganizationType"


class OrganizationAgentV2:
    """Organization agent wrapper with permissive output validation."""

    async def run(  # noqa: C901, PLR0912, PLR0915
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        warnings: list[str] = []
        org_name = _resolve_org_name(context)
        github_lookup_enabled = context.get("github_lookup_enabled")
        if not isinstance(github_lookup_enabled, bool):
            github_lookup_enabled = True

        github_org: dict[str, Any] = {}
        if github_lookup_enabled:
            github_org = providers.github.get_organization(org_name)

        ror_record: dict[str, Any] | None = None
        if providers.ror:
            ror_id_hint = context.get("ror_id")
            try:
                if isinstance(ror_id_hint, str) and ror_id_hint:
                    ror_record = providers.ror.get_organization(ror_id_hint)
                else:
                    ror_query = (
                        context.get("ror_query")
                        or github_org.get("name")
                        or github_org.get("login")
                        or org_name
                    )
                    ror_matches = providers.ror.search_organizations(str(ror_query))
                    if ror_matches:
                        ror_record = ror_matches[0]
            except ProviderNotFoundError as exc:
                warnings.append(f"ROR lookup failed: {exc}")
        else:
            warnings.append("ROR provider not configured for organization enrichment")

        infoscience_match: dict[str, Any] | None = None
        if providers.infoscience:
            infoscience_query = context.get("infoscience_query") or github_org.get("name") or org_name
            infoscience_results = providers.infoscience.search_orgunit(str(infoscience_query))
            infoscience_match = _pick_best_orgunit_match(infoscience_results)
        else:
            warnings.append("Infoscience provider not configured for organization enrichment")

        ror_id = ror_record.get("id") if isinstance(ror_record, dict) else None
        infoscience_id = (
            infoscience_match.get("infoscienceOrgUnitIdentifier")
            if isinstance(infoscience_match, dict)
            else None
        )
        github_handle: str | None = None
        github_login = github_org.get("login")
        if isinstance(github_login, str) and github_login:
            github_handle = github_login
        elif github_lookup_enabled:
            github_handle = org_name
        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        identifier_hierarchy: list[tuple[str, str | None]] = [
            ("pulse:ror", ror_id if isinstance(ror_id, str) else None),
            (
                "pulse:infoscienceOrganizationIdentifier",
                infoscience_id if isinstance(infoscience_id, str) else None,
            ),
            ("pulse:githubOrganizationHandle", github_handle),
            ("uuid", uuid_value),
        ]
        id_source, resolved_id = next(
            (identifier, value)
            for identifier, value in identifier_hierarchy
            if value
        )

        ror_types = []
        if isinstance(ror_record, dict) and isinstance(ror_record.get("types"), list):
            ror_types = [item for item in ror_record["types"] if isinstance(item, str)]

        organization_type = _classify_organization_type(ror_types)
        parent_org = None
        has_units: list[str] = []
        if isinstance(ror_record, dict):
            relationships = ror_record.get("relationships")
            if isinstance(relationships, dict):
                parent_payload = relationships.get("parent")
                if isinstance(parent_payload, dict):
                    parent_id = parent_payload.get("id")
                    if isinstance(parent_id, str):
                        parent_org = parent_id
                children_payload = relationships.get("children")
                if isinstance(children_payload, list):
                    for child in children_payload:
                        if not isinstance(child, dict):
                            continue
                        child_id = child.get("id")
                        if isinstance(child_id, str):
                            has_units.append(child_id)

        if not parent_org and isinstance(infoscience_match, dict):
            parent_candidate = infoscience_match.get("parentOrganization")
            if isinstance(parent_candidate, str) and parent_candidate:
                parent_org = parent_candidate

        repositories = context.get("repositories")
        source_repositories = context.get("source_repositories")
        owns: list[str] = []
        if isinstance(source_repositories, list):
            owns = [value for value in source_repositories if isinstance(value, str) and value]
        elif isinstance(repositories, list):
            owns = [value for value in repositories if isinstance(value, str) and value]
        elif github_lookup_enabled and isinstance(github_org.get("repositories"), list) and github_handle:
            owns = [
                f"{github_handle}/{repo_name}"
                for repo_name in github_org["repositories"]
                if isinstance(repo_name, str) and repo_name
            ]

        payload = {
            "id": resolved_id,
            "type": "org:Organization",
            "shacl": "pulse:OrganizationShape",
            "identifiers": {
                "pulse:ror": ror_id if isinstance(ror_id, str) else None,
                "pulse:infoscienceOrganizationIdentifier": (
                    infoscience_id if isinstance(infoscience_id, str) else None
                ),
                "pulse:githubOrganizationHandle": github_handle,
                "uuid": uuid_value,
            },
            "idSource": id_source,
            "schema:name": (
                (ror_record or {}).get("name")
                or (infoscience_match or {}).get("name")
                or github_org.get("name")
                or org_name
            ),
            "schema:identifier": ror_id if isinstance(ror_id, str) else None,
            "pulse:githubOrganizationHandle": github_handle,
            "pulse:infoscienceOrganizationIdentifier": (
                infoscience_id if isinstance(infoscience_id, str) else None
            ),
            "pulse:OrganizationType": organization_type,
            "pulse:githubOrgFollowers": github_org.get("followers"),
            "org:hasUnit": has_units,
            "org:unitOf": parent_org,
            "pulse:owns": owns,
        }

        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        raw_output = deepcopy(payload)
        validated_payload, validation_warnings = validate_permissive(
            payload,
            schema_name="organization",
        )
        warnings.extend(validation_warnings)

        derivation_stats = {
            "organization_id": validated_payload.get("id"),
            "organization_name": validated_payload.get("schema:name"),
            "github_lookup_enabled": github_lookup_enabled,
            "source_repositories": (
                deepcopy(source_repositories)
                if isinstance(source_repositories, list)
                else []
            ),
            "owned_repositories": deepcopy(owns),
            "parent_organization": parent_org,
            "unit_ids": deepcopy(has_units),
            "ror_types": deepcopy(ror_types),
        }

        return AgentResult(
            data=validated_payload,
            warnings=warnings,
            raw_output=raw_output,
            stats={"derivation": derivation_stats},
        )
