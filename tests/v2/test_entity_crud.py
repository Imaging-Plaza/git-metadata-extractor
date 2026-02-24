from __future__ import annotations

import sqlite3
import time

import pytest

from src.v2.graph.store import GraphStore


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "entity_crud.db"))


def test_insert_entity_then_get_entity_round_trip(tmp_path) -> None:
    store = _build_store(tmp_path)

    inserted_id = store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace"},
        identifiers={"orcid": "0000-0001-2345-6789"},
        id_source="orcid",
    )
    entity = store.get_entity(inserted_id)

    assert entity is not None
    assert entity.id == "person-1"
    assert entity.type == "person"
    assert entity.data == {"name": "Ada Lovelace"}
    assert entity.identifiers == {"orcid": "0000-0001-2345-6789"}
    assert entity.id_source == "orcid"


def test_get_non_existent_entity_returns_none(tmp_path) -> None:
    store = _build_store(tmp_path)

    assert store.get_entity("missing-entity") is None


def test_get_entities_by_type_returns_only_matching_rows(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "A"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )
    store.insert_entity(
        entity_type="organization",
        entity_id="org-1",
        data={"name": "EPFL"},
        identifiers={"ror": "https://ror.org/04dp5v874"},
        id_source="ror",
    )
    store.insert_entity(
        entity_type="person",
        entity_id="person-2",
        data={"name": "B"},
        identifiers={"orcid": "0000-0000-0000-0002"},
        id_source="orcid",
    )

    people = store.get_entities_by_type("person")

    assert [entity.id for entity in people] == ["person-1", "person-2"]


def test_update_entity_changes_data_and_last_seen(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )
    first = store.get_entity("person-1")
    assert first is not None

    time.sleep(0.01)
    updated = store.update_entity(
        "person-1",
        data={"name": "Ada Lovelace", "role": "Mathematician"},
    )
    second = store.get_entity("person-1")
    assert second is not None

    assert updated is True
    assert second.data == {"name": "Ada Lovelace", "role": "Mathematician"}
    assert second.last_seen > first.last_seen


def test_delete_entity_removes_row(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-delete",
        data={"name": "Delete Me"},
        identifiers={"orcid": "0000-0000-0000-0003"},
        id_source="orcid",
    )

    deleted = store.delete_entity("person-delete")

    assert deleted is True
    assert store.get_entity("person-delete") is None


def test_entity_insert_is_transactional_on_integrity_error(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="organization",
        entity_id="org-1",
        data={"name": "Original"},
        identifiers={"ror": "https://ror.org/04dp5v874"},
        id_source="ror",
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.insert_entity(
            entity_type="organization",
            entity_id="org-1",
            data={"name": "Conflicting"},
            identifiers={"ror": "https://ror.org/999999999"},
            id_source="ror",
        )

    entity = store.get_entity("org-1")
    assert entity is not None
    assert entity.data == {"name": "Original"}
