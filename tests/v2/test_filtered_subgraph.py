from __future__ import annotations

from typing import Any

from src.v2.graph.export import ENTITY_URI_PREFIX, JSONLDExporter
from src.v2.graph.store import GraphStore

SOURCE_URL_PRIMARY = "https://github.com/owner/repo-a"
SOURCE_URL_SECONDARY = "https://github.com/owner/repo-b"


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "filtered_subgraph.db"))


def _raw_entity_id(value: str) -> str:
    if value.startswith(ENTITY_URI_PREFIX):
        return value[len(ENTITY_URI_PREFIX) :]
    return value


def _graph_entity_ids(graph_payload: dict[str, Any]) -> set[str]:
    entity_ids: set[str] = set()
    for node in graph_payload.get("@graph", []):
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id")
        if isinstance(node_id, str):
            entity_ids.add(_raw_entity_id(node_id))
    return entity_ids


def _entity_refs(node: dict[str, Any]) -> set[str]:
    refs: set[str] = set()

    def _collect(payload: Any) -> None:
        if isinstance(payload, dict):
            ref_id = payload.get("@id")
            if isinstance(ref_id, str):
                refs.add(_raw_entity_id(ref_id))
            for value in payload.values():
                _collect(value)
        elif isinstance(payload, list):
            for item in payload:
                _collect(item)

    _collect(node)
    return refs


def _seed_store(store: GraphStore) -> None:
    for entity_type, entity_id, name in (
        ("person", "person-1", "Ada"),
        ("person", "person-2", "Grace"),
        ("organization", "org-1", "EPFL"),
        ("repository", "repo-1", "project-a"),
        ("repository", "repo-2", "project-b"),
    ):
        store.insert_entity(
            entity_type=entity_type,
            entity_id=entity_id,
            data={"schema:name": name},
            identifiers={},
            id_source="test",
        )

    store.insert_edge("person-1", "repo-1", "contributes_to")
    store.insert_edge("person-1", "repo-2", "contributes_to")
    store.insert_edge("org-1", "repo-1", "owns")
    store.insert_edge("person-2", "repo-2", "contributes_to")

    run_primary = store.create_run(SOURCE_URL_PRIMARY, "repository")
    store.complete_run(
        run_primary,
        stats={"entity_ids": ["person-1", "repo-1", "org-1"]},
    )
    run_secondary = store.create_run(SOURCE_URL_SECONDARY, "repository")
    store.complete_run(
        run_secondary,
        stats={"entity_ids": ["person-2", "repo-2"]},
    )


def test_filter_by_entity_type_returns_only_matching_entities(tmp_path) -> None:
    store = _build_store(tmp_path)
    _seed_store(store)
    exporter = JSONLDExporter()

    filtered = exporter.export_filtered(
        store.get_rdf_graph(),
        entity_types=["Person"],
        store=store,
    )

    assert _graph_entity_ids(filtered) == {"person-1", "person-2"}


def test_filter_by_source_url_returns_run_entities(tmp_path) -> None:
    store = _build_store(tmp_path)
    _seed_store(store)
    exporter = JSONLDExporter()

    filtered = exporter.export_filtered(
        store.get_rdf_graph(),
        source_url=SOURCE_URL_PRIMARY,
        store=store,
    )

    assert _graph_entity_ids(filtered) == {"person-1", "repo-1", "org-1"}


def test_combined_filter_returns_intersection(tmp_path) -> None:
    store = _build_store(tmp_path)
    _seed_store(store)
    exporter = JSONLDExporter()

    filtered = exporter.export_filtered(
        store.get_rdf_graph(),
        source_url=SOURCE_URL_PRIMARY,
        entity_types=["Person"],
        store=store,
    )

    assert _graph_entity_ids(filtered) == {"person-1"}


def test_edges_between_included_entities_are_kept(tmp_path) -> None:
    store = _build_store(tmp_path)
    _seed_store(store)
    exporter = JSONLDExporter()

    filtered = exporter.export_filtered(
        store.get_rdf_graph(),
        source_url=SOURCE_URL_PRIMARY,
        store=store,
    )
    person_node = next(
        node
        for node in filtered["@graph"]
        if isinstance(node, dict) and _raw_entity_id(str(node.get("@id"))) == "person-1"
    )

    assert "repo-1" in _entity_refs(person_node)


def test_edges_to_excluded_entities_are_pruned(tmp_path) -> None:
    store = _build_store(tmp_path)
    _seed_store(store)
    exporter = JSONLDExporter()

    filtered = exporter.export_filtered(
        store.get_rdf_graph(),
        source_url=SOURCE_URL_PRIMARY,
        store=store,
    )
    person_node = next(
        node
        for node in filtered["@graph"]
        if isinstance(node, dict) and _raw_entity_id(str(node.get("@id"))) == "person-1"
    )

    assert "repo-2" not in _entity_refs(person_node)


def test_empty_filter_result_returns_valid_envelope(tmp_path) -> None:
    store = _build_store(tmp_path)
    _seed_store(store)
    exporter = JSONLDExporter()

    filtered = exporter.export_filtered(
        store.get_rdf_graph(),
        source_url="https://github.com/unknown/repo",
        store=store,
    )

    assert "@context" in filtered
    assert filtered["@graph"] == []


def test_no_filter_matches_full_graph_export(tmp_path) -> None:
    store = _build_store(tmp_path)
    _seed_store(store)
    exporter = JSONLDExporter()

    full_graph = exporter.export_full_graph(store.get_rdf_graph())
    filtered = exporter.export_filtered(store.get_rdf_graph(), store=store)

    assert filtered == full_graph
