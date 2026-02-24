from __future__ import annotations

import uuid

from src.v2.canonicalization import resolve_person_id

UUID_V5_VERSION = 5


def test_resolve_person_id_prefers_orcid() -> None:
    person = {
        "identifiers": {
            "pulse:orcid": "0000-0002-1825-0097",
            "pulse:infosciencePersonIdentifier": "12345",
            "pulse:githubUsername": "johndoe",
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    assert canonical_id == "https://orcid.org/0000-0002-1825-0097"
    assert id_source == "orcid"


def test_resolve_person_id_uses_infoscience_when_orcid_missing() -> None:
    person = {
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": "12345",
            "pulse:githubUsername": "johndoe",
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    assert canonical_id == "https://infoscience.epfl.ch/server/api/core/items/12345"
    assert id_source == "infosciencePersonIdentifier"


def test_resolve_person_id_normalizes_infoscience_entity_url_with_full_suffix() -> None:
    person = {
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": (
                "https://infoscience.epfl.ch/entities/person/"
                "cc69e432-9742-4ebd-a318-02a491f44e69/full"
            ),
            "pulse:githubUsername": "johndoe",
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    assert canonical_id == (
        "https://infoscience.epfl.ch/server/api/core/items/"
        "cc69e432-9742-4ebd-a318-02a491f44e69"
    )
    assert id_source == "infosciencePersonIdentifier"


def test_resolve_person_id_normalizes_infoscience_core_items_url() -> None:
    person = {
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": (
                "https://infoscience.epfl.ch/server/api/core/items/"
                "cc69e432-9742-4ebd-a318-02a491f44e69"
            ),
            "pulse:githubUsername": "johndoe",
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    assert canonical_id == (
        "https://infoscience.epfl.ch/server/api/core/items/"
        "cc69e432-9742-4ebd-a318-02a491f44e69"
    )
    assert id_source == "infosciencePersonIdentifier"


def test_resolve_person_id_uses_github_when_higher_priority_ids_are_missing() -> None:
    person = {
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": "johndoe",
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    assert canonical_id == "https://github.com/johndoe"
    assert id_source == "githubUsername"


def test_resolve_person_id_generates_stable_uuid_v5_fallback() -> None:
    person = {
        "schema:name": "Jane Doe",
        "schema:email": "jane@example.org",
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": None,
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    parsed = uuid.UUID(canonical_id)
    assert parsed.version == UUID_V5_VERSION
    assert id_source == "uuid"


def test_resolve_person_id_is_deterministic_for_same_input() -> None:
    person = {
        "schema:name": "Jane Doe",
        "schema:email": "jane@example.org",
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": None,
        },
    }

    first_id, first_source = resolve_person_id(person)
    second_id, second_source = resolve_person_id(person)

    assert first_id == second_id
    assert first_source == second_source == "uuid"


def test_resolve_person_id_is_idempotent_for_pre_resolved_payload() -> None:
    person = {
        "id": "https://orcid.org/0000-0002-1825-0097",
        "idSource": "orcid",
        "identifiers": {
            "pulse:orcid": "0000-0002-1825-0097",
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    assert canonical_id == "https://orcid.org/0000-0002-1825-0097"
    assert id_source == "orcid"
