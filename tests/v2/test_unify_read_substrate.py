"""Tests for `unify.runner.read_substrate`, the unifier's read path.

Everything in `test_unify.py` starts from hand-built `Record`s, because
`unify_records` is the I/O-free decision path. This is the step before it: the
one `SELECT` that turns the accumulated substrate into those records. Kept in
its own file because it is store-boundary I/O, driven the way
`test_oxigraph_store.py` drives the client — a real `OxigraphStore` over
`httpx.MockTransport` returning SPARQL JSON results — rather than a fake store
with its own idea of the binding format.

A mock cannot evaluate the query, so the `urn:pulse:output:` filter is not
asserted here: every row the transport returns is one the store would have
returned. What is asserted is what the reader does with those rows.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from git_metadata_extractor.store.oxigraph import OxigraphStore
from git_metadata_extractor.unify.runner import read_substrate

if TYPE_CHECKING:
    from git_metadata_extractor.unify.cluster import Record

RUN_A = "urn:pulse:output:run-a:github"
RUN_B = "urn:pulse:output:run-b:github"
JANE = "https://github.com/jane"
SCHEMA = "http://schema.org/"
PULSE = "https://open-pulse.epfl.ch/ontology#"
ROR = "https://ror.org/02s376052"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
XSD_INTEGER = "http://www.w3.org/2001/XMLSchema#integer"
PERSON = f"{SCHEMA}Person"


def _uri(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _literal(value: str, datatype: str | None = None) -> dict[str, str]:
    entry = {"type": "literal", "value": value}
    if datatype:
        entry["datatype"] = datatype
    return entry


def _row(
    prop: str,
    obj: dict[str, str],
    *,
    graph: str = RUN_A,
    subject: str = JANE,
    entity_type: str = PERSON,
) -> dict[str, Any]:
    return {
        "g": _uri(graph),
        "s": _uri(subject),
        "type": _uri(entity_type),
        "p": _uri(prop),
        "o": obj,
    }


def _read(rows: list[dict[str, Any]]) -> list[Record]:
    body = json.dumps({"head": {"vars": []}, "results": {"bindings": rows}})
    store = OxigraphStore(
        "http://oxigraph:7878",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text=body)),
    )
    return asyncio.run(read_substrate(store))


def test_the_type_row_is_the_entity_type_not_a_property() -> None:
    """`?s a ?type . ?s ?p ?o` also binds `?p` to `rdf:type` itself.

    That row is already consumed as the record's type. Keeping it as a property
    would hand the merger an `rdf:type` value to union or select, and write it
    into a closed canonical shape that declares no such slot.
    """
    records = _read(
        [
            _row(RDF_TYPE, _uri(PERSON)),
            _row(f"{SCHEMA}name", _literal("Jane")),
        ],
    )

    assert [(record.entity_type, record.properties) for record in records] == [
        ("schema:Person", {"schema:name": ["Jane"]}),
    ]


def test_one_subject_in_two_graphs_is_two_records() -> None:
    """The graph is the provenance handle, so it cannot be merged away here.

    Two runs describing one IRI are two records until clustering decides
    otherwise; folding them on read would make every cross-run cluster look
    single-source and leave provenance nothing to attribute.
    """
    records = _read(
        [
            _row(f"{SCHEMA}name", _literal("jane"), graph=RUN_A),
            _row(f"{SCHEMA}name", _literal("Jane Doe"), graph=RUN_B),
        ],
    )

    assert sorted((r.graph, r.iri, r.values("schema:name")[0]) for r in records) == [
        (RUN_A, JANE, "jane"),
        (RUN_B, JANE, "Jane Doe"),
    ]


def test_repeated_property_values_accumulate() -> None:
    """One row per value: a multi-valued property arrives as several rows."""
    records = _read(
        [
            _row(f"{PULSE}hasProfile", _uri("urn:pulse:profile:github:jane")),
            _row(f"{PULSE}hasProfile", _uri("urn:pulse:profile:gitlab:jane")),
        ],
    )

    assert records[0].properties == {
        "pulse:hasProfile": [
            {"@id": "urn:pulse:profile:github:jane"},
            {"@id": "urn:pulse:profile:gitlab:jane"},
        ],
    }


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        # A reference must look like one, or the merger's `_is_reference` and
        # the remap pass never see it as an edge.
        pytest.param(_uri(ROR), {"@id": ROR}, id="iri"),
        pytest.param(_literal("Jane"), "Jane", id="plain-literal"),
        # `pulse:contributionCount` is `MAX`-merged: compared as strings,
        # "9" beats "40".
        pytest.param(_literal("40", XSD_INTEGER), 40, id="xsd-integer"),
        # One ill-typed literal in the store must not abort the whole pass.
        pytest.param(_literal("forty", XSD_INTEGER), "forty", id="ill-typed-integer"),
    ],
)
def test_objects_arrive_in_the_shape_the_merger_expects(obj: dict[str, str], expected: Any) -> None:
    records = _read([_row(f"{SCHEMA}value", obj)])

    assert records[0].properties == {"schema:value": [expected]}


@pytest.mark.parametrize("missing", ["g", "s", "type", "p"])
def test_a_row_missing_a_binding_is_skipped(missing: str) -> None:
    """Without all four there is no record to put the value on, or no key."""
    incomplete = _row(f"{SCHEMA}name", _literal("ghost"), subject="https://github.com/ghost")
    del incomplete[missing]

    records = _read([_row(f"{SCHEMA}name", _literal("Jane")), incomplete])

    assert [(record.iri, record.properties) for record in records] == [
        (JANE, {"schema:name": ["Jane"]}),
    ]
