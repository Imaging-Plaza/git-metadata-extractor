from __future__ import annotations

import json
from typing import Any

from rdflib import Graph, Literal, URIRef

from src.v2.graph.export import JSONLDExporter
from src.v2.pipeline.stages.jsonld_build import ENTITY_URI_PREFIX, build_jsonld_output
from src.v2.pipeline.stages.models import AssembledOutput


def _context() -> dict[str, Any]:
    payload = JSONLDExporter().get_context()
    raw_context = payload.get("@context")
    assert isinstance(raw_context, dict)
    return raw_context


def _node_by_id(payload: dict[str, Any], *, node_id: str) -> dict[str, Any]:
    graph = payload.get("@graph")
    assert isinstance(graph, list)
    for node in graph:
        if isinstance(node, dict) and node.get("@id") == node_id:
            return node
    raise AssertionError


def test_build_jsonld_output_preserves_literal_fields_even_when_matching_entity_ids() -> None:
    assembled = AssembledOutput(
        root_entity={
            "id": "owner/repo",
            "type": "schema:SoftwareSourceCode",
            "schema:name": "owner/repo",
            "schema:author": ["supermaxiste"],
        },
        related_entities=[
            {
                "id": "supermaxiste",
                "type": "schema:Person",
                "schema:name": "supermaxiste",
                "pulse:githubUsername": "supermaxiste",
            },
        ],
    )

    payload = build_jsonld_output(
        assembled=assembled,
        jsonld_context=_context(),
    )

    repo_node = _node_by_id(
        payload,
        node_id=f"{ENTITY_URI_PREFIX}owner/repo",
    )
    person_node = _node_by_id(
        payload,
        node_id=f"{ENTITY_URI_PREFIX}supermaxiste",
    )

    assert repo_node["schema:author"] == [{"@id": f"{ENTITY_URI_PREFIX}supermaxiste"}]
    assert person_node["schema:name"] == "supermaxiste"
    assert person_node["pulse:githubUsername"] == "supermaxiste"


def test_build_jsonld_output_emits_schema_url_as_iri_node() -> None:
    assembled = AssembledOutput(
        root_entity={
            "id": "alice",
            "type": "schema:Person",
            "schema:name": "Alice",
            "schema:url": "https://github.com/alice",
        },
    )
    payload = build_jsonld_output(
        assembled=assembled,
        jsonld_context=_context(),
    )

    graph = Graph()
    graph.parse(data=json.dumps(payload), format="json-ld")

    subject = URIRef(f"{ENTITY_URI_PREFIX}alice")
    predicate = URIRef("http://schema.org/url")
    objects = list(graph.objects(subject, predicate))

    assert len(objects) == 1
    assert objects[0] == URIRef("https://github.com/alice")
    assert not isinstance(objects[0], Literal)


def test_build_jsonld_output_emits_license_citation_and_org_hierarchy_as_iris() -> None:
    assembled = AssembledOutput(
        root_entity={
            "id": "owner/repo",
            "type": "schema:SoftwareSourceCode",
            "schema:name": "owner/repo",
            "schema:author": ["alice"],
            "schema:license": "https://spdx.org/licenses/MIT.html",
            "schema:citation": "https://doi.org/10.1234/example",
        },
        related_entities=[
            {
                "id": "alice",
                "type": "schema:Person",
                "schema:name": "Alice",
                "pulse:githubUsername": "alice",
            },
            {
                "id": "https://ror.org/02s376052",
                "type": "org:Organization",
                "schema:name": "EPFL",
                "org:hasUnit": ["https://github.com/sdsc-ordes"],
            },
            {
                "id": "https://github.com/sdsc-ordes",
                "type": "org:Organization",
                "schema:name": "sdsc-ordes",
                "org:unitOf": "https://ror.org/02s376052",
            },
        ],
    )
    payload = build_jsonld_output(
        assembled=assembled,
        jsonld_context=_context(),
    )

    graph = Graph()
    graph.parse(data=json.dumps(payload), format="json-ld")

    repo_subject = URIRef(f"{ENTITY_URI_PREFIX}owner/repo")
    org_parent = URIRef("https://ror.org/02s376052")
    org_child = URIRef("https://github.com/sdsc-ordes")

    assert list(graph.objects(repo_subject, URIRef("http://schema.org/license"))) == [
        URIRef("https://spdx.org/licenses/MIT.html"),
    ]
    assert list(graph.objects(repo_subject, URIRef("http://schema.org/citation"))) == [
        URIRef("https://doi.org/10.1234/example"),
    ]
    assert list(graph.objects(org_parent, URIRef("http://www.w3.org/ns/org#hasUnit"))) == [
        org_child,
    ]
    assert list(graph.objects(org_child, URIRef("http://www.w3.org/ns/org#unitOf"))) == [
        org_parent,
    ]
