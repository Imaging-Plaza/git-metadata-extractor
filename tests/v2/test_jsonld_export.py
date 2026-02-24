from __future__ import annotations

import json
from typing import Any

from pyld import jsonld
from rdflib import Graph

from src.v2.graph.export import ENTITY_URI_PREFIX, JSONLDExporter
from src.v2.graph.store import GraphStore
from src.v2.testing.mock_generator import generate_dataset

REALISTIC_ENTITY_COUNT = 33


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "jsonld_export.db"))


def _node_for_entity(
    graph_payload: dict[str, Any],
    *,
    entity_id: str,
) -> dict[str, Any] | None:
    for node in graph_payload.get("@graph", []):
        if not isinstance(node, dict):
            continue
        raw_node_id = node.get("@id")
        if not isinstance(raw_node_id, str):
            continue
        if raw_node_id in {entity_id, f"{ENTITY_URI_PREFIX}{entity_id}"}:
            return node
    return None


def _contains_entity_reference(payload: Any, *, entity_id: str) -> bool:
    candidate_ids = {entity_id, f"{ENTITY_URI_PREFIX}{entity_id}"}
    if isinstance(payload, dict):
        ref_id = payload.get("@id")
        if isinstance(ref_id, str) and ref_id in candidate_ids:
            return True
        return any(
            _contains_entity_reference(value, entity_id=entity_id)
            for value in payload.values()
        )
    if isinstance(payload, list):
        return any(
            _contains_entity_reference(value, entity_id=entity_id)
            for value in payload
        )
    return False


def test_export_full_graph_has_context_with_required_prefixes(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"schema:name": "Ada"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )
    exporter = JSONLDExporter()

    exported = exporter.export_full_graph(store.get_rdf_graph())
    context = exported["@context"]

    assert {"schema", "pulse", "org", "rdf", "rdfs", "xsd", "wd"}.issubset(context)


def test_exported_graph_contains_expected_id_type_and_prefixed_properties(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={
            "schema:name": "Ada Lovelace",
            "pulse:githubUsername": "ada-lovelace",
        },
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )
    exporter = JSONLDExporter()

    exported = exporter.export_full_graph(store.get_rdf_graph())
    person_node = _node_for_entity(exported, entity_id="person-1")

    assert person_node is not None
    assert "@id" in person_node
    assert "pulse:githubUsername" in person_node
    raw_type = person_node.get("@type")
    if isinstance(raw_type, str):
        assert raw_type.endswith("Person")
    else:
        assert isinstance(raw_type, list)
        assert any(
            isinstance(type_value, str) and type_value.endswith("Person")
            for type_value in raw_type
        )


def test_export_of_mock_dataset_is_valid_jsonld_and_roundtrips(tmp_path) -> None:
    store = _build_store(tmp_path)
    dataset = generate_dataset(seed=42, persons=6, repos=5, orgs=6, github_orgs=3, articles=4)
    mock_entities = [
        *dataset["persons"],
        *dataset["repositories"],
        *dataset["organizations"],
        *dataset["memberships"],
        *dataset["contributions"],
        *dataset["articles"],
    ]

    assert len(mock_entities) >= REALISTIC_ENTITY_COUNT
    for payload in mock_entities[:REALISTIC_ENTITY_COUNT]:
        identifiers = payload.get("identifiers")
        store.insert_entity(
            entity_type=str(payload.get("type", "unknown")),
            entity_id=str(payload["id"]),
            data=payload,
            identifiers=identifiers if isinstance(identifiers, dict) else {},
            id_source=str(payload.get("idSource", "uuid")),
        )

    exporter = JSONLDExporter()
    exported = exporter.export_full_graph(store.get_rdf_graph())

    expanded_payload = jsonld.expand(exported)
    assert isinstance(expanded_payload, list)

    roundtrip_graph = Graph()
    roundtrip_graph.parse(data=json.dumps(exported), format="json-ld")
    assert len(roundtrip_graph) == len(store.get_rdf_graph())


def test_export_entity_includes_only_requested_entity_triples(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"schema:name": "Ada"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )
    store.insert_entity(
        entity_type="repository",
        entity_id="repo-1",
        data={"schema:name": "project"},
        identifiers={"github": "owner/repo"},
        id_source="github",
    )
    store.insert_edge("person-1", "repo-1", "contributes_to")
    exporter = JSONLDExporter()

    exported = exporter.export_entity("person-1", store.get_rdf_graph())

    assert len(exported["@graph"]) == 1
    person_node = _node_for_entity(exported, entity_id="person-1")
    assert person_node is not None
    assert not _contains_entity_reference(person_node, entity_id="repo-1")
