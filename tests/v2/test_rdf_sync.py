from __future__ import annotations

import json

from rdflib import Literal, URIRef
from rdflib.namespace import RDF

from src.v2.graph.rdf_sync import RDFGraphSync
from src.v2.graph.store import GraphStore
from src.v2.testing.mock_generator import generate_dataset

REALISTIC_ENTITY_COUNT = 33


def _build_store(tmp_path, filename: str = "rdf_sync.db") -> GraphStore:
    return GraphStore(str(tmp_path / filename))


def test_load_from_empty_store_returns_empty_graph(tmp_path) -> None:
    store = _build_store(tmp_path)
    sync = RDFGraphSync()

    graph = sync.load_from_store(store)

    assert len(graph) == 0


def test_load_from_store_with_five_entities_populates_expected_triples(tmp_path) -> None:
    store = _build_store(tmp_path)
    sync = RDFGraphSync()
    entities = [
        {
            "entity_type": "person",
            "entity_id": "person-1",
            "data": {"schema:name": "Ada", "pulse:githubUsername": "ada"},
            "identifiers": {"orcid": "0000-0000-0000-0001"},
            "id_source": "orcid",
        },
        {
            "entity_type": "person",
            "entity_id": "person-2",
            "data": {"schema:name": "Grace", "pulse:githubUsername": "grace"},
            "identifiers": {"orcid": "0000-0000-0000-0002"},
            "id_source": "orcid",
        },
        {
            "entity_type": "organization",
            "entity_id": "org-1",
            "data": {"schema:name": "EPFL"},
            "identifiers": {"ror": "https://ror.org/04dp5v874"},
            "id_source": "ror",
        },
        {
            "entity_type": "repository",
            "entity_id": "repo-1",
            "data": {"schema:name": "project-a", "pulse:discipline": ["wd:Q413"]},
            "identifiers": {"github": "acme/project-a"},
            "id_source": "github",
        },
        {
            "entity_type": "repository",
            "entity_id": "repo-2",
            "data": {"schema:name": "project-b", "pulse:discipline": ["wd:Q420"]},
            "identifiers": {"github": "acme/project-b"},
            "id_source": "github",
        },
    ]

    expected_triples = 0
    for payload in entities:
        store.insert_entity(**payload)
        expected_triples += len(
            sync.entity_to_triples(
                payload["entity_type"],
                {
                    "id": payload["entity_id"],
                    "type": payload["entity_type"],
                    **payload["data"],
                    "identifiers": payload["identifiers"],
                },
            ),
        )

    graph = sync.load_from_store(store)

    assert len(graph) == expected_triples


def test_upsert_entity_updates_sqlite_and_in_memory_graph(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"schema:name": "Ada"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    store.upsert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"schema:name": "Ada Lovelace"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
        source="person-agent",
        run_id="run-1",
    )
    entity = store.get_entity("person-1")
    graph = store.get_rdf_graph()

    assert entity is not None
    assert entity.data["schema:name"] == "Ada Lovelace"
    assert (
        URIRef("urn:git-metadata-extractor:entity:person-1"),
        URIRef("http://schema.org/name"),
        Literal("Ada Lovelace"),
    ) in graph


def test_delete_entity_removes_entity_and_edge_triples_from_graph(tmp_path) -> None:
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
        data={"schema:name": "project-a"},
        identifiers={"github": "acme/project-a"},
        id_source="github",
    )
    edge_id = store.insert_edge(
        source_id="person-1",
        target_id="repo-1",
        relation_type="contributes_to",
    )
    assert store.get_edge(edge_id) is not None

    deleted = store.delete_entity("person-1")
    graph = store.get_rdf_graph()
    person_uri = URIRef("urn:git-metadata-extractor:entity:person-1")

    assert deleted is True
    assert store.get_entity("person-1") is None
    assert list(graph.triples((person_uri, None, None))) == []
    assert list(graph.triples((None, None, person_uri))) == []


def test_graph_serializes_to_valid_jsonld(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"schema:name": "Ada"},
        identifiers={"orcid": "0000-0000-0000-0001"},
        id_source="orcid",
    )

    serialized = store.get_rdf_graph().serialize(format="json-ld")
    payload = json.loads(serialized)

    assert isinstance(payload, (list, dict))


def test_entity_to_triples_uses_ontology_namespace_mappings() -> None:
    sync = RDFGraphSync()

    triples = sync.entity_to_triples(
        "person",
        {
            "id": "person-1",
            "type": "pulse:Person",
            "schema:name": "Ada",
            "pulse:githubUsername": "ada",
        },
    )
    predicates = {predicate for _, predicate, _ in triples}
    type_values = {obj for _, predicate, obj in triples if predicate == RDF.type}

    assert URIRef("http://schema.org/name") in predicates
    assert URIRef("https://open-pulse.epfl.ch/ontology#githubUsername") in predicates
    assert URIRef("https://open-pulse.epfl.ch/ontology#Person") in type_values


def test_entity_to_triples_normalizes_builtin_entity_types() -> None:
    sync = RDFGraphSync()

    triples = sync.entity_to_triples(
        "person",
        {
            "id": "person-1",
            "schema:name": "Ada",
        },
    )
    type_values = {obj for _, predicate, obj in triples if predicate == RDF.type}

    assert URIRef("https://open-pulse.epfl.ch/ontology#Person") in type_values


def test_startup_load_with_mock_dataset_33_entities(tmp_path) -> None:
    db_path = tmp_path / "rdf_sync_realistic.db"
    store = GraphStore(str(db_path))
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

    reloaded_store = GraphStore(str(db_path))

    assert len(reloaded_store.get_all_entities()) == REALISTIC_ENTITY_COUNT
    assert len(reloaded_store.get_rdf_graph()) > 0
