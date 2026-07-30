from __future__ import annotations

import uuid
from typing import Any, Callable

from git_metadata_extractor.canonicalization import resolve_person_id
from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator

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
    assert id_source == "pulse:orcid"


def test_resolve_person_id_uses_infoscience_when_orcid_missing() -> None:
    person = {
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": "cc69e432-9742-4ebd-a318-02a491f44e69",
            "pulse:githubUsername": "johndoe",
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    assert canonical_id == (
        "https://infoscience.epfl.ch/entities/person/"
        "cc69e432-9742-4ebd-a318-02a491f44e69"
    )
    assert id_source == "pulse:infosciencePersonIdentifier"


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
        "https://infoscience.epfl.ch/entities/person/"
        "cc69e432-9742-4ebd-a318-02a491f44e69"
    )
    assert id_source == "pulse:infosciencePersonIdentifier"


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
        "https://infoscience.epfl.ch/entities/person/"
        "cc69e432-9742-4ebd-a318-02a491f44e69"
    )
    assert id_source == "pulse:infosciencePersonIdentifier"


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
    assert id_source == "pulse:githubUsername"


def test_resolve_person_id_generates_uuid4_fallback() -> None:
    # The fallback resolver used to emit a deterministic uuid5 derived
    # from the entity body, which caused cross-repo collisions when two
    # persons in unrelated contexts shared the same minimal seed
    # ("John Doe", no ORCID, no GitHub handle). It now emits a uuid4 —
    # entities without a grounding identifier are NOT comparable across
    # repos, so a fresh URN per call is the correct semantics.
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
    assert parsed.version == 4
    assert id_source == "uuid"


def test_resolve_person_id_fallback_yields_distinct_uuids_per_call() -> None:
    # Counterpart to the uuid5-determinism guarantee that the fallback
    # used to provide. With uuid4 the inverse property is required:
    # two calls with identical input MUST produce distinct UUIDs, so
    # downstream graph-stores don't merge unrelated entities.
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

    assert first_id != second_id
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
    assert id_source == "pulse:orcid"


def test_resolve_person_id_canonicalizes_pre_resolved_github_handle() -> None:
    person = {
        "id": "johndoe",
        "idSource": "pulse:githubUsername",
        "identifiers": {
            "pulse:githubUsername": "johndoe",
        },
    }

    canonical_id, id_source = resolve_person_id(person)

    assert canonical_id == "https://github.com/johndoe"
    assert id_source == "pulse:githubUsername"


def test_resolve_person_id_output_is_strict_enum_compatible(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    person = load_fixture("schema/strict", "pulse_PersonShape")[0]
    person["id"] = "https://orcid.org/0000-0002-1825-0097"
    person["idSource"] = resolve_person_id(person)[1]

    result = validator.validate("person", person)

    assert result.is_valid is True
