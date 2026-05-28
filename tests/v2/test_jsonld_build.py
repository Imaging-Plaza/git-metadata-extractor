from __future__ import annotations

import json
from typing import Any

from rdflib import Graph, Literal, URIRef

from src.v2.pipeline.stages.jsonld_build import ENTITY_URI_PREFIX, build_jsonld_output
from src.v2.pipeline.stages.models import AssembledOutput
from src.v2.schema import load_jsonld_context


def _context() -> dict[str, Any]:
    return load_jsonld_context()


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


def _assembled_with_internal_fields() -> AssembledOutput:
    return AssembledOutput(
        root_entity={
            "id": "owner/repo",
            "type": "schema:SoftwareSourceCode",
            "schema:name": "owner/repo",
            "_homepage": "https://example.org",
            "_watchers_count": 42,
        },
        related_entities=[],
        excluded_entities=[
            {
                "entity_type": "person",
                "entity": {
                    "id": "alice",
                    "type": "schema:Person",
                    "schema:name": "Alice",
                    "_bio": "researcher",
                },
                "reason": [{"message": "strict_validation"}],
            },
        ],
    )


def test_internal_fields_dropped_when_flag_off() -> None:
    payload = build_jsonld_output(
        assembled=_assembled_with_internal_fields(),
        jsonld_context=_context(),
        include_internal_fields=False,
    )
    node = payload["@graph"][0]
    excluded = payload["excluded_entities"][0]["entity"]
    assert "gme-internal" not in payload["@context"]
    assert not [k for k in node if str(k).startswith(("_", "gme-internal:"))]
    assert not [k for k in excluded if str(k).startswith(("_", "gme-internal:"))]


def test_internal_fields_renamed_to_gme_internal_terms_when_flag_on() -> None:
    payload = build_jsonld_output(
        assembled=_assembled_with_internal_fields(),
        jsonld_context=_context(),
        include_internal_fields=True,
    )
    # Prefix registered so the terms expand to real IRIs.
    assert (
        payload["@context"]["gme-internal"]
        == "https://openpulse.science/git-metadata-extractor#"
    )
    node = payload["@graph"][0]
    assert node["gme-internal:homepage"] == "https://example.org"
    assert node["gme-internal:watchers_count"] == 42
    assert "_homepage" not in node
    # Applies to excluded_entities too.
    excluded = payload["excluded_entities"][0]["entity"]
    assert excluded["gme-internal:bio"] == "researcher"
    assert "_bio" not in excluded

    # The renamed terms expand to IRIs under the gme-internal namespace.
    graph = Graph()
    graph.parse(data=json.dumps(payload), format="json-ld")
    assert (
        None,
        URIRef("https://openpulse.science/git-metadata-extractor#homepage"),
        Literal("https://example.org"),
    ) in graph


def _assembled_with_publiccode() -> AssembledOutput:
    return AssembledOutput(
        root_entity={
            "id": "owner/repo",
            "type": "schema:SoftwareSourceCode",
            "schema:name": "owner/repo",
            "_publiccode": {
                "publiccodeYmlVersion": "0.4.0",
                "name": "Foodsoft",
                "url": "https://github.com/foodcoops/foodsoft.git",
                "softwareType": "standalone/web",
                "developmentStatus": "stable",
                "platforms": ["web"],
                "categories": ["e-commerce", "warehouse-management"],
                "legal": {"license": "AGPL-3.0-or-later"},
                "maintenance": {"type": "community"},
            },
        },
        related_entities=[],
        excluded_entities=[],
    )


def test_publiccode_scalars_hoisted_to_publiccode_namespace_when_flag_on() -> None:
    """Top-level scalar/list publiccode fields should appear under
    `publiccode:<field>` predicates so SPARQL can query them directly,
    while the structured sub-trees (legal, maintenance) stay inside the
    `gme-internal:publiccode` JSON blob for consumers that want them."""
    payload = build_jsonld_output(
        assembled=_assembled_with_publiccode(),
        jsonld_context=_context(),
        include_internal_fields=True,
    )
    assert payload["@context"]["publiccode"] == "https://yml.publiccode.tools/"
    node = payload["@graph"][0]
    assert node["publiccode:name"] == "Foodsoft"
    assert node["publiccode:url"] == "https://github.com/foodcoops/foodsoft.git"
    assert node["publiccode:softwareType"] == "standalone/web"
    assert node["publiccode:developmentStatus"] == "stable"
    assert node["publiccode:platforms"] == ["web"]
    assert node["publiccode:categories"] == ["e-commerce", "warehouse-management"]
    # Structured sub-trees stay nested under gme-internal:publiccode —
    # they're not safe to flatten generically.
    pc_blob = node["gme-internal:publiccode"]
    assert pc_blob["legal"] == {"license": "AGPL-3.0-or-later"}
    assert pc_blob["maintenance"] == {"type": "community"}


def test_publiccode_dropped_entirely_when_flag_off() -> None:
    """`include_internal_fields=False` strips the publiccode predicates
    along with everything else `_`-prefixed, keeping the public output
    SHACL-conformant."""
    payload = build_jsonld_output(
        assembled=_assembled_with_publiccode(),
        jsonld_context=_context(),
        include_internal_fields=False,
    )
    assert "publiccode" not in payload["@context"]
    node = payload["@graph"][0]
    assert not [k for k in node if str(k).startswith("publiccode:")]
    assert not [k for k in node if str(k).startswith("gme-internal:")]


def test_publiccode_predicates_expand_to_rdf_triples() -> None:
    """Sanity-check that rdflib expands the `publiccode:` prefix into
    real IRI predicates, so the payload is uploadable as RDF without
    any extra context plumbing."""
    payload = build_jsonld_output(
        assembled=_assembled_with_publiccode(),
        jsonld_context=_context(),
        include_internal_fields=True,
    )
    graph = Graph()
    graph.parse(data=json.dumps(payload), format="json-ld")
    repo_subject = URIRef(f"{ENTITY_URI_PREFIX}owner/repo")
    # Scalar predicate.
    assert (
        repo_subject,
        URIRef("https://yml.publiccode.tools/softwareType"),
        Literal("standalone/web"),
    ) in graph
    # List-of-strings: each value becomes its own triple.
    categories = set(
        graph.objects(
            repo_subject,
            URIRef("https://yml.publiccode.tools/categories"),
        ),
    )
    assert categories == {Literal("e-commerce"), Literal("warehouse-management")}


def test_publiccode_no_hoist_when_publiccode_field_missing() -> None:
    """An entity without `_publiccode` should produce no `publiccode:`
    predicates — the hoist is conditional on the parsed payload being
    present."""
    assembled = AssembledOutput(
        root_entity={
            "id": "owner/repo",
            "type": "schema:SoftwareSourceCode",
            "schema:name": "owner/repo",
            "_homepage": "https://example.org",
        },
        related_entities=[],
        excluded_entities=[],
    )
    payload = build_jsonld_output(
        assembled=assembled,
        jsonld_context=_context(),
        include_internal_fields=True,
    )
    # The `publiccode:` prefix is still registered in the context — that's
    # fine, an unused prefix is cheap and keeps the @context stable across
    # responses — but no triples carry the prefix.
    node = payload["@graph"][0]
    assert not [k for k in node if str(k).startswith("publiccode:")]


def test_publiccode_empty_payload_emits_no_predicates() -> None:
    """`_publiccode = None` or `_publiccode = {}` produce zero
    `publiccode:` predicates even when the flag is on."""
    for empty in (None, {}):
        assembled = AssembledOutput(
            root_entity={
                "id": "owner/repo",
                "type": "schema:SoftwareSourceCode",
                "schema:name": "owner/repo",
                "_publiccode": empty,
            },
            related_entities=[],
            excluded_entities=[],
        )
        payload = build_jsonld_output(
            assembled=assembled,
            jsonld_context=_context(),
            include_internal_fields=True,
        )
        node = payload["@graph"][0]
        assert not [k for k in node if str(k).startswith("publiccode:")]
