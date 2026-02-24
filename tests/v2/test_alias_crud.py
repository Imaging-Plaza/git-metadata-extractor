from __future__ import annotations

import pytest

from src.v2.graph.store import GraphStore

CONFIDENCE_EXPECTED = 0.87


def _build_store(tmp_path) -> GraphStore:
    store = GraphStore(str(tmp_path / "alias_crud.db"))
    store.insert_entity(
        entity_type="organization",
        entity_id="org-123",
        data={"name": "EPFL"},
        identifiers={"ror": "https://ror.org/04dp5v874"},
        id_source="ror",
    )
    store.insert_entity(
        entity_type="organization",
        entity_id="org-456",
        data={"name": "Other Org"},
        identifiers={"ror": "https://ror.org/03yrm5c26"},
        id_source="ror",
    )
    return store


def test_insert_alias_and_lookup_returns_canonical_entity(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_alias(
        alias_string="EPFL",
        canonical_entity_id="org-123",
        confidence=0.95,
        source="ror",
    )

    match = store.lookup_alias("EPFL")

    assert match is not None
    assert match.alias_string == "EPFL"
    assert match.canonical_entity_id == "org-123"


def test_alias_lookup_is_case_insensitive(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_alias(
        alias_string="Swiss Federal Institute of Technology Lausanne",
        canonical_entity_id="org-123",
        confidence=0.9,
        source="manual",
    )

    match = store.lookup_alias("swiss federal institute of technology lausanne")

    assert match is not None
    assert match.canonical_entity_id == "org-123"


def test_get_aliases_for_entity_returns_all_aliases(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_alias("EPFL", "org-123", 1.0, "ror")
    store.insert_alias("Ecole Polytechnique Federale de Lausanne", "org-123", 0.9, "agent")

    aliases = store.get_aliases_for_entity("org-123")

    assert {alias.alias_string for alias in aliases} == {
        "EPFL",
        "Ecole Polytechnique Federale de Lausanne",
    }


def test_duplicate_alias_insert_is_idempotent_for_same_entity(tmp_path) -> None:
    store = _build_store(tmp_path)

    first_id = store.insert_alias("EPFL", "org-123", 1.0, "ror")
    second_id = store.insert_alias("epfl", "org-123", 0.8, "agent")

    assert second_id == first_id
    aliases = store.get_aliases_for_entity("org-123")
    assert len(aliases) == 1


def test_duplicate_alias_for_different_entity_is_rejected(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_alias("EPFL", "org-123", 1.0, "ror")

    with pytest.raises(ValueError, match="already mapped"):
        store.insert_alias("epfl", "org-456", 0.7, "manual")


def test_alias_confidence_and_source_are_stored_and_returned(tmp_path) -> None:
    store = _build_store(tmp_path)
    alias_id = store.insert_alias(
        alias_string="Ecole Polytechnique Federale de Lausanne",
        canonical_entity_id="org-123",
        confidence=CONFIDENCE_EXPECTED,
        source="derived",
    )

    match = store.lookup_alias("ecole polytechnique federale de lausanne")
    assert match is not None
    assert match.alias_id == alias_id
    assert match.confidence == CONFIDENCE_EXPECTED
    assert match.source == "derived"


def test_delete_alias_removes_alias_entry(tmp_path) -> None:
    store = _build_store(tmp_path)
    alias_id = store.insert_alias("EPFL", "org-123", 1.0, "ror")

    deleted = store.delete_alias(alias_id)
    match = store.lookup_alias("EPFL")

    assert deleted is True
    assert match is None
