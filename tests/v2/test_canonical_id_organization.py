from __future__ import annotations

import uuid
from typing import Any, Callable

from src.v2.canonicalization import resolve_organization_id
from src.v2.validation.schema_validation import StrictSchemaValidator

UUID_V5_VERSION = 5


def test_resolve_organization_id_prefers_ror() -> None:
    organization = {
        "identifiers": {
            "pulse:ror": "05gzmn429",
            "pulse:infoscienceOrganizationIdentifier": "org-123",
            "pulse:githubOrganizationHandle": "epfl-center-imaging",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == "https://ror.org/05gzmn429"
    assert id_source == "pulse:ror"


def test_resolve_organization_id_uses_infoscience_when_ror_missing() -> None:
    organization = {
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": "6a95499f-7def-427d-ba0a-1ff2a27f58f6",
            "pulse:githubOrganizationHandle": "epfl-center-imaging",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == (
        "https://infoscience.epfl.ch/entities/orgunit/"
        "6a95499f-7def-427d-ba0a-1ff2a27f58f6"
    )
    assert id_source == "pulse:infoscienceOrganizationIdentifier"


def test_resolve_organization_id_normalizes_infoscience_api_url_with_full_suffix() -> None:
    organization = {
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": (
                "https://infoscience.epfl.ch/server/api/entities/orgunit/"
                "6a95499f-7def-427d-ba0a-1ff2a27f58f6/full"
            ),
            "pulse:githubOrganizationHandle": "epfl-center-imaging",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == (
        "https://infoscience.epfl.ch/entities/orgunit/"
        "6a95499f-7def-427d-ba0a-1ff2a27f58f6"
    )
    assert id_source == "pulse:infoscienceOrganizationIdentifier"


def test_resolve_organization_id_normalizes_infoscience_core_items_url() -> None:
    organization = {
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": (
                "https://infoscience.epfl.ch/server/api/core/items/"
                "6a95499f-7def-427d-ba0a-1ff2a27f58f6"
            ),
            "pulse:githubOrganizationHandle": "epfl-center-imaging",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == (
        "https://infoscience.epfl.ch/entities/orgunit/"
        "6a95499f-7def-427d-ba0a-1ff2a27f58f6"
    )
    assert id_source == "pulse:infoscienceOrganizationIdentifier"


def test_resolve_organization_id_uses_github_when_higher_priority_ids_are_missing() -> None:
    organization = {
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": "epfl-center-imaging",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == "https://github.com/epfl-center-imaging"
    assert id_source == "pulse:githubOrganizationHandle"


def test_resolve_organization_id_generates_uuid4_fallback() -> None:
    # Fallback now emits uuid4 (was uuid5) — see canonicalization
    # id_resolution `_deterministic_uuid` for the cross-repo collision
    # rationale.
    organization = {
        "schema:name": "Imagining Center",
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": None,
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    parsed = uuid.UUID(canonical_id)
    assert parsed.version == 4
    assert id_source == "uuid"


def test_resolve_organization_id_fallback_yields_distinct_uuids_per_call() -> None:
    # Two unrelated orgs with the same minimal seed must get DIFFERENT
    # URNs so downstream graph-stores don't merge them.
    organization = {
        "schema:name": "Imagining Center",
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": None,
        },
    }

    first_id, first_source = resolve_organization_id(organization)
    second_id, second_source = resolve_organization_id(organization)

    assert first_id != second_id
    assert first_source == second_source == "uuid"


def test_resolve_organization_id_canonicalizes_pre_resolved_github_handle() -> None:
    organization = {
        "id": "epfl-center-imaging",
        "idSource": "pulse:githubOrganizationHandle",
        "identifiers": {
            "pulse:githubOrganizationHandle": "epfl-center-imaging",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == "https://github.com/epfl-center-imaging"
    assert id_source == "pulse:githubOrganizationHandle"


def test_resolve_organization_id_overrides_inconsistent_existing_id_source_when_ror_exists() -> None:
    organization = {
        "id": "https://infoscience.epfl.ch/server/api/core/items/95372c6b-7d45-432e-a84e-660c9fa54e05",
        "idSource": "pulse:infoscienceOrganizationIdentifier",
        "identifiers": {
            "pulse:ror": "https://ror.org/02hdt9m26",
            "pulse:infoscienceOrganizationIdentifier": "95372c6b-7d45-432e-a84e-660c9fa54e05",
            "pulse:githubOrganizationHandle": "sdsc-ordes",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == "https://ror.org/02hdt9m26"
    assert id_source == "pulse:ror"


def test_resolve_organization_id_keeps_existing_resolution_when_consistent() -> None:
    organization = {
        "id": "https://ror.org/02hdt9m26",
        "idSource": "pulse:ror",
        "identifiers": {
            "pulse:ror": "02hdt9m26",
            "pulse:infoscienceOrganizationIdentifier": "95372c6b-7d45-432e-a84e-660c9fa54e05",
            "pulse:githubOrganizationHandle": "sdsc-ordes",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == "https://ror.org/02hdt9m26"
    assert id_source == "pulse:ror"


def test_resolve_organization_id_output_is_strict_enum_compatible(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    organization = load_fixture("schema/strict", "pulse_OrganizationShape")[0]
    organization["id"] = "https://ror.org/05gzmn429"
    organization["idSource"] = resolve_organization_id(organization)[1]

    result = validator.validate("organization", organization)

    assert result.is_valid is True
