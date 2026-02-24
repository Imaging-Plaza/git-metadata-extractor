from __future__ import annotations

import sqlite3

import pytest

from src.v2.graph.store import GraphStore


def _build_store(tmp_path) -> GraphStore:
    store = GraphStore(str(tmp_path / "edge_crud.db"))
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"name": "Ada"},
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
        entity_type="repository",
        entity_id="repo-1",
        data={"name": "git-metadata-extractor"},
        identifiers={"github": "Imaging-Plaza/git-metadata-extractor"},
        id_source="github",
    )
    return store


def test_insert_edge_between_existing_entities(tmp_path) -> None:
    store = _build_store(tmp_path)

    edge_id = store.insert_edge(
        source_id="person-1",
        target_id="repo-1",
        relation_type="contributes_to",
        data={"commit_count": 4},
    )
    edges = store.get_edges_by_source("person-1")

    assert len(edges) == 1
    assert edges[0].id == edge_id
    assert edges[0].target_id == "repo-1"
    assert edges[0].relation_type == "contributes_to"
    assert edges[0].data == {"commit_count": 4}


def test_get_edges_by_source_returns_outgoing_edges(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_edge("person-1", "repo-1", "contributes_to")
    store.insert_edge("person-1", "org-1", "member_of")
    store.insert_edge("org-1", "repo-1", "owns")

    outgoing = store.get_edges_by_source("person-1")

    assert {(edge.source_id, edge.target_id) for edge in outgoing} == {
        ("person-1", "repo-1"),
        ("person-1", "org-1"),
    }


def test_get_edges_by_target_returns_incoming_edges(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_edge("person-1", "repo-1", "contributes_to")
    store.insert_edge("org-1", "repo-1", "owns")

    incoming = store.get_edges_by_target("repo-1")

    assert {(edge.source_id, edge.target_id) for edge in incoming} == {
        ("person-1", "repo-1"),
        ("org-1", "repo-1"),
    }


def test_get_edges_by_type_filters_relation_type(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_edge("person-1", "repo-1", "contributes_to")
    store.insert_edge("person-1", "org-1", "member_of")

    memberships = store.get_edges_by_type("member_of")

    assert len(memberships) == 1
    assert memberships[0].relation_type == "member_of"
    assert memberships[0].target_id == "org-1"


def test_insert_edge_rejects_non_existent_entity_references(tmp_path) -> None:
    store = _build_store(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        store.insert_edge("missing-entity", "repo-1", "contributes_to")


def test_delete_edge_removes_it_from_queries(tmp_path) -> None:
    store = _build_store(tmp_path)
    edge_id = store.insert_edge("person-1", "repo-1", "contributes_to")

    deleted = store.delete_edge(edge_id)
    remaining = store.get_edges_by_source("person-1")

    assert deleted is True
    assert remaining == []
