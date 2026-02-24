from __future__ import annotations

import json
import time
from dataclasses import asdict

from src.v2.graph.store import GraphStore

EXPECTED_CHANGED_FIELDS = 2


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "provenance.db"))


def test_upsert_with_two_field_changes_creates_two_entries(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada", "title": "Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "title": "Senior Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-42",
    )
    entries = store.get_provenance("person-1")

    assert len(entries) == EXPECTED_CHANGED_FIELDS
    assert {entry.field for entry in entries} == {"data.name", "data.title"}
    assert all(entry.entity_id == "person-1" for entry in entries)
    assert all(entry.source == "person-agent" for entry in entries)
    assert all(entry.run_id == "run-42" for entry in entries)
    assert all(entry.timestamp is not None for entry in entries)


def test_get_provenance_returns_entries_ordered_by_timestamp(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada", "title": "Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )
    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "title": "Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-42",
    )
    time.sleep(0.01)
    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "title": "Senior Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-42",
    )

    entries = store.get_provenance("person-1")

    assert [entry.field for entry in entries] == ["data.name", "data.title"]
    assert entries[0].timestamp <= entries[1].timestamp


def test_get_provenance_for_field_filters_history(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada", "title": "Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )
    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "title": "Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-42",
    )
    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "title": "Senior Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-42",
    )

    field_entries = store.get_provenance_for_field("person-1", "data.title")

    assert len(field_entries) == 1
    assert field_entries[0].field == "data.title"
    assert field_entries[0].old_value == "Researcher"
    assert field_entries[0].new_value == "Senior Researcher"


def test_upsert_without_changes_creates_no_provenance(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "title": "Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "title": "Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-42",
    )

    assert store.get_provenance("person-1") == []


def test_provenance_entries_are_json_serializable(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada", "title": "Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )
    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada Lovelace", "title": "Senior Researcher"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-42",
    )
    entries = store.get_provenance("person-1")

    payload = [
        {
            **asdict(entry),
            "timestamp": entry.timestamp.isoformat(),
        }
        for entry in entries
    ]

    serialized = json.dumps(payload)
    assert "data.name" in serialized
