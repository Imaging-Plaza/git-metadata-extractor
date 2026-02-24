from __future__ import annotations

import uuid

from src.v2.canonicalization import resolve_organization_id

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
    assert id_source == "ror"


def test_resolve_organization_id_uses_infoscience_when_ror_missing() -> None:
    organization = {
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": "12345",
            "pulse:githubOrganizationHandle": "epfl-center-imaging",
        },
    }

    canonical_id, id_source = resolve_organization_id(organization)

    assert canonical_id == "https://infoscience.epfl.ch/server/api/core/items/12345"
    assert id_source == "infoscienceOrganizationIdentifier"


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
        "https://infoscience.epfl.ch/server/api/core/items/"
        "6a95499f-7def-427d-ba0a-1ff2a27f58f6"
    )
    assert id_source == "infoscienceOrganizationIdentifier"


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
        "https://infoscience.epfl.ch/server/api/core/items/"
        "6a95499f-7def-427d-ba0a-1ff2a27f58f6"
    )
    assert id_source == "infoscienceOrganizationIdentifier"


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
    assert id_source == "githubOrganizationHandle"


def test_resolve_organization_id_generates_stable_uuid_v5_fallback() -> None:
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
    assert parsed.version == UUID_V5_VERSION
    assert id_source == "uuid"


def test_resolve_organization_id_is_deterministic() -> None:
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

    assert first_id == second_id
    assert first_source == second_source == "uuid"
