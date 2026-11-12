"""JSON-LD roundtrip gate: the v2 builder's output, read back as RDF.

`build_jsonld_output` serialises the v2 intermediate against the hand-written
v2 context (`load_jsonld_context`). That JSON-LD is what `/v2/extract` returns
with `V2_CANONICAL_OUTPUT_ENABLED=false`, and what `shacl_gate` parses and
validates against the v2.1.2 bundle when the canonical projection did not run.

The context is where a property gets lost in translation without anything
erroring: drop `@type: @id` from a term and its references read back as string
literals, drop `@type: xsd:date` and a date becomes an untyped string, switch a
container to `@list` and a set of values collapses into one list node.

So every property of every strict fixture goes through the real builder and
the real context, is parsed with rdflib, and is checked against the term kind
its v2.1.2 SHACL property shape declares. The shapes are the independent
statement of what each property's objects must be; the context is the thing
under test.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

import pytest
from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef

from git_metadata_extractor.pipeline.stages.jsonld_build import (
    ENTITY_URI_PREFIX,
    build_jsonld_output,
)
from git_metadata_extractor.pipeline.stages.models import AssembledOutput
from git_metadata_extractor.schema import load_jsonld_context
from git_metadata_extractor.validation.ontology import load_ontology_shapes_graph

if TYPE_CHECKING:
    from rdflib.term import Node

SH = Namespace("http://www.w3.org/ns/shacl#")

# How the strict fixtures' CURIEs expand: the namespaces the v2.1.2 shapes use.
# Deliberately not read from the context, which is what is being tested.
PREFIXES = {
    "schema": "http://schema.org/",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "org": "http://www.w3.org/ns/org#",
    "time": "http://www.w3.org/2006/time#",
}

STRICT_FIXTURES = (
    "pulse_PersonShape",
    "pulse_OrganizationShape",
    "pulse_RepositoryShape",
    "pulse_MembershipShape",
    "pulse_ContributionShape",
    "pulse_ArticleShape",
)


def _expand(curie: str) -> URIRef:
    prefix, _, local = curie.partition(":")
    return URIRef(PREFIXES[prefix] + local)


def _subject(entity_id: str) -> URIRef:
    if entity_id.startswith(("http://", "https://", "urn:")):
        return URIRef(entity_id)
    return URIRef(f"{ENTITY_URI_PREFIX}{entity_id}")


def _term_kind(term: Node) -> str:
    if isinstance(term, Literal):
        # A plain JSON string is JSON-LD's xsd:string; rdflib leaves it implicit.
        return f"literal {term.datatype or XSD.string}"
    if isinstance(term, URIRef):
        return "IRI"
    return type(term).__name__


def _declared_kind(shapes: Graph, property_shape: Node) -> str:
    datatype = shapes.value(property_shape, SH.datatype)
    if datatype is not None:
        return f"literal {datatype}"
    if shapes.value(property_shape, SH.nodeKind) == SH.Literal:
        return "literal"
    # sh:nodeKind sh:IRI, sh:class, or an sh:or over classes: a reference.
    return "IRI"


def _property_failures(
    *,
    objects: set[Node],
    values: list[Any],
    declared: str,
    where: str,
) -> list[str]:
    """What went wrong between one property's input values and its triples."""
    failures: list[str] = []
    distinct = {json.dumps(item, sort_keys=True) for item in values}
    if len(objects) != len(distinct):
        failures.append(
            f"{where}: {len(distinct)} value(s) in, {len(objects)} triple(s) out",
        )
    for term in objects:
        actual = _term_kind(term)
        if declared != (actual.split()[0] if declared == "literal" else actual):
            failures.append(f"{where}: {actual}, shape declares {declared}")
    if declared.startswith("literal") and objects:
        # Rebuilt with the datatype it arrived with, so rdflib normalises
        # both sides alike (`...Z` reads back `...+00:00`).
        first = next(iter(objects))
        datatype = first.datatype if isinstance(first, Literal) else None
        if objects != {Literal(str(item), datatype=datatype) for item in values}:
            failures.append(f"{where}: literal value changed on the way")
    return failures


def _property_shapes(shapes: Graph, target_class: URIRef) -> dict[Node, Node]:
    node_shape = shapes.value(predicate=SH.targetClass, object=target_class)
    assert node_shape is not None, f"no v2.1.2 shape targets {target_class}"
    by_path: dict[Node, Node] = {}
    for property_shape in shapes.objects(node_shape, SH.property):
        path = shapes.value(property_shape, SH.path)
        assert path is not None, f"{property_shape} has no sh:path"
        by_path[path] = property_shape
    return by_path


@pytest.fixture(scope="module")
def roundtripped(load_fixture: Callable[[str, str], Any]) -> Graph:
    """All strict fixtures in one build, so cross-entity references resolve."""
    entities = [
        entity for name in STRICT_FIXTURES for entity in load_fixture("schema/strict", name)
    ]
    payload = build_jsonld_output(
        assembled=AssembledOutput(root_entity=None, related_entities=entities),
        jsonld_context=load_jsonld_context(),
    )
    return Graph().parse(data=json.dumps(payload), format="json-ld")


@pytest.mark.parametrize("fixture_name", STRICT_FIXTURES)
def test_every_strict_property_survives_with_the_term_kind_its_shape_declares(
    fixture_name: str,
    load_fixture: Callable[[str, str], Any],
    roundtripped: Graph,
) -> None:
    shapes = load_ontology_shapes_graph()
    entities = load_fixture("schema/strict", fixture_name)
    target_class = _expand(entities[0]["type"])
    property_shapes = _property_shapes(shapes, target_class)

    failures: list[str] = []
    exercised: set[Node] = set()
    for entity in entities:
        subject = _subject(entity["id"])
        if (subject, RDF.type, target_class) not in roundtripped:
            failures.append(f"{subject}: lost its rdf:type {target_class}")
        # Strict properties are the CURIE keys; `id`, `type` and the
        # `shacl` / `identifiers` / `idSource` envelope are not.
        for key, value in entity.items():
            values = value if isinstance(value, list) else [value]
            if ":" not in key or value is None or not values:
                continue
            predicate = _expand(key)
            property_shape = property_shapes.get(predicate)
            if property_shape is None:
                failures.append(f"{key}: not declared by the shape for {target_class}")
                continue
            exercised.add(predicate)
            failures.extend(
                _property_failures(
                    objects=set(roundtripped.objects(subject, predicate)),
                    values=values,
                    declared=_declared_kind(shapes, property_shape),
                    where=f"{subject} {key}",
                ),
            )

    assert failures == [], "\n".join(failures)
    # Otherwise a property the fixtures never populate is one this gate
    # silently cannot see, and the test name would overclaim.
    assert set(property_shapes) <= exercised, (
        f"no {fixture_name} fixture exercises {sorted(map(str, set(property_shapes) - exercised))}"
    )
