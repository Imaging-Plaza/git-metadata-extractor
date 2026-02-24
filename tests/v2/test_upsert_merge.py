from __future__ import annotations

from src.v2.graph.store import GraphStore

EXPECTED_PROVENANCE_ENTRIES = 2


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "upsert_merge.db"))


def _has_field(changed_fields: list[str], field: str) -> bool:
    return field in changed_fields or f"data.{field}" in changed_fields


def test_upsert_new_entity_inserts_as_is(tmp_path) -> None:
    store = _build_store(tmp_path)

    result = store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
    )
    entity = store.get_entity("person-1")

    assert entity is not None
    assert entity.data == {"name": "Ada Lovelace", "affiliations": ["EPFL"]}
    assert entity.identifiers == {"orcid": "0000-0000-0000-0001"}
    assert result.changed_fields == []


def test_upsert_same_data_reports_no_changes(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    result = store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
    )

    assert result.changed_fields == []
    assert result.provenance_updates == []


def test_upsert_unions_list_fields_without_duplicates(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    result = store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL", "CERN"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
    )
    entity = store.get_entity("person-1")

    assert entity is not None
    assert entity.data["affiliations"] == ["EPFL", "CERN"]
    assert _has_field(result.changed_fields, "affiliations")


def test_upsert_promotes_non_null_incoming_scalar_over_null_existing(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": None},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
    )
    entity = store.get_entity("person-1")

    assert entity is not None
    assert entity.data["name"] == "Ada Lovelace"


def test_upsert_preserves_existing_scalar_when_incoming_is_null(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    result = store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": None},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
    )
    entity = store.get_entity("person-1")

    assert entity is not None
    assert entity.data["name"] == "Ada Lovelace"
    assert result.changed_fields == []


def test_upsert_preserves_authoritative_identifier_on_conflict(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace"},
        identifiers={"orcid": "9999-9999-9999-9999"},
        id_source="orcid",
        source="person-agent",
    )
    entity = store.get_entity("person-1")

    assert entity is not None
    assert entity.identifiers["orcid"] == "0000-0000-0000-0001"


def test_upsert_emits_provenance_for_each_changed_field(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada", "affiliations": ["EPFL"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    result = store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL", "CERN"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-123",
    )
    provenance = store.get_provenance("person-1")

    assert len(result.provenance_updates) == EXPECTED_PROVENANCE_ENTRIES
    assert len(provenance) == EXPECTED_PROVENANCE_ENTRIES
    assert {entry.field for entry in provenance} == {"data.name", "data.affiliations"}
    assert all(entry.source == "person-agent" for entry in provenance)
    assert all(entry.run_id == "run-123" for entry in provenance)


def test_upsert_is_idempotent_for_repeated_payload(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL", "CERN"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    first = store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL", "CERN"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
    )
    second = store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "affiliations": ["EPFL", "CERN"]},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
    )

    assert first.changed_fields == []
    assert second.changed_fields == []
    assert store.get_provenance("person-1") == []
