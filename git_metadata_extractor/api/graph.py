"""Read the accumulated graph: entity lookup, provenance lookup, inventory.

`PROVENANCE_ARCHITECTURE.md` phase 7, and the last of the seven. Three
endpoints over the store the substrate writer and unifier populate:

    GET /v2/graph/status                  what the store holds
    GET /v2/graph/entity?iri=...          one canonical entity, as JSON-LD
    GET /v2/graph/provenance?subject=...  why each chosen value won

**New endpoints, not a reinterpretation of `/v2/extract`.** The decision on
record is *"substrate slice + Phase 9 query API; version the endpoint rather
than reinterpret it"*, so `/v2/extract` keeps meaning "extract this URL now"
and these mean "tell me what the store knows". The two answer different
questions and a caller has to be able to ask each.

**No SPARQL is accepted from callers.** Every query here is fixed text with
IRIs substituted through `store.terms.iri_term`, which refuses anything RDF
forbids in an IRIREF rather than escaping it. That matters more than usual:
Oxigraph ships **no authentication** and its `/store` endpoint is writable by
anyone who can reach it, so an endpoint that proxied a caller's query string
would be an unauthenticated write primitive one `INSERT` away. There is no
query parameter to abuse, and `test_no_endpoint_accepts_a_sparql_string` keeps
it that way.

**Provenance cannot be JSON-LD**, and that is not a shortcut. `graph:prov`
holds RDF-star quoted triples; the pinned rdflib cannot serialise one, so the
response is built from SPARQL bindings — a flat list of records, not a graph.
The entity endpoint *is* JSON-LD, because `graph:canonical` is plain RDF.

**What real data will show today:** `/entity` works, and `/provenance` returns
an empty list for every subject, because the 119-run corpus produces zero
contested values (§3k). The fixtures in `tests/v2/test_graph_api.py` are what
exercise the provenance path.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, Query, status
from fastapi.responses import JSONResponse

from git_metadata_extractor import config
from git_metadata_extractor.api_models import (
    V2ErrorResponse,
    V2ErrorType,
    V2GraphEntityResponse,
    V2GraphProvenanceResponse,
    V2GraphStatusResponse,
    V2ProvenanceRecord,
)
from git_metadata_extractor.auth import verify_token
from git_metadata_extractor.schema import load_generated_context
from git_metadata_extractor.store import oxigraph
from git_metadata_extractor.store.terms import (
    UnusableIRIError,
    compact,
    expand,
    iri_term,
)
from git_metadata_extractor.unify.provenance import (
    FIRST_OBSERVED_ON,
    LAST_CONFIRMED_ON,
    OBSERVATION_COUNT,
    OBSERVATION_KIND,
    OBSERVED_ON,
    PROV_GRAPH,
    SAME_AS_PROPERTIES,
    WAS_DERIVED_FROM,
)
from git_metadata_extractor.unify.runner import CANONICAL_GRAPH

from ._router import v2_router

logger = logging.getLogger(__name__)

MAX_LIMIT = 500


def _store() -> oxigraph.OxigraphStore | None:
    return oxigraph.store_from_config(
        config.substrate_store_url(),
        timeout=config.substrate_store_timeout_seconds(),
    )


def _no_store() -> JSONResponse:
    """503, not 404: the graph is not empty, it is unreachable.

    A caller that cannot tell "this store holds nothing about that IRI" from
    "there is no store configured" will cache the wrong answer.
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=V2ErrorResponse(
            error_type=V2ErrorType.PIPELINE_ERROR,
            detail=(
                "no graph store configured; set V2_SUBSTRATE_STORE_URL to the "
                "Oxigraph server root"
            ),
        ).model_dump(mode="json"),
    )


def _bad_iri(exc: UnusableIRIError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=V2ErrorResponse(
            error_type=V2ErrorType.VALIDATION_ERROR,
            detail=str(exc),
        ).model_dump(mode="json"),
    )


def _binding(row: dict[str, Any], name: str) -> str | None:
    entry = row.get(name)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    return str(value) if value is not None else None


def _typed_value(entry: dict[str, Any]) -> Any:
    """A SPARQL object binding as the JSON-LD form the canonical layer uses."""
    value = entry.get("value")
    if value is None:
        return None
    if entry.get("type") == "uri":
        return {"@id": compact(str(value))}
    datatype = str(entry.get("datatype") or "")
    if datatype.endswith(("#integer", "#int", "#long")):
        try:
            return int(value)
        except ValueError:
            return str(value)
    return str(value)


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------


@v2_router.get("/graph/status", response_model=V2GraphStatusResponse)
async def graph_status(
    _token: Annotated[str, Depends(verify_token)],
) -> Any:
    """What the store holds: graph counts, triples, entities by type."""
    store = _store()
    if store is None:
        return _no_store()
    if not await store.is_available():
        return _no_store()

    graphs = await store.named_graphs()
    by_type_rows = await store.select(
        f"""
        SELECT ?type (COUNT(*) AS ?n) WHERE {{
          GRAPH <{CANONICAL_GRAPH}> {{ ?s a ?type }}
        }} GROUP BY ?type ORDER BY DESC(?n)
        """,
    )
    run_rows = await store.select(
        """
        PREFIX pulse: <https://open-pulse.epfl.ch/ontology#>
        SELECT (COUNT(DISTINCT ?run) AS ?n) WHERE {
          GRAPH ?g { ?run a pulse:ExtractionRun }
        }
        """,
    )
    return V2GraphStatusResponse(
        store_url=store.base_url,
        named_graphs=len(graphs),
        triples=sum(graphs.values()),
        canonical_triples=graphs.get(CANONICAL_GRAPH, 0),
        provenance_triples=graphs.get(PROV_GRAPH, 0),
        extraction_runs=int(_binding(run_rows[0], "n") or 0) if run_rows else 0,
        canonical_entities_by_type={
            compact(str(_binding(row, "type"))): int(_binding(row, "n") or 0)
            for row in by_type_rows
            if _binding(row, "type")
        },
    )


# --------------------------------------------------------------------------
# one canonical entity
# --------------------------------------------------------------------------


@v2_router.get("/graph/entity", response_model=V2GraphEntityResponse)
async def graph_entity(
    _token: Annotated[str, Depends(verify_token)],
    iri: Annotated[str, Query(description="The entity's canonical IRI")],
) -> Any:
    """One entity from `graph:canonical`, as a JSON-LD node.

    JSON-LD because the canonical graph is plain RDF and this is the shape the
    rest of the service speaks. Predicates come back compacted and references
    as `{"@id": ...}`, so the node is the same shape `/v2/extract` returns for
    the same entity — which is the point: a caller should not need two parsers.
    """
    store = _store()
    if store is None:
        return _no_store()
    try:
        subject = iri_term(iri)
    except UnusableIRIError as exc:
        return _bad_iri(exc)

    rows = await store.select(
        f"SELECT ?p ?o WHERE {{ GRAPH <{CANONICAL_GRAPH}> {{ {subject} ?p ?o }} }}",
    )
    if not rows:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=V2ErrorResponse(
                error_type=V2ErrorType.NOT_FOUND,
                detail=f"no entity {iri} in {CANONICAL_GRAPH}",
            ).model_dump(mode="json"),
        )

    node: dict[str, Any] = {"@id": iri}
    for row in rows:
        prop = _binding(row, "p")
        value = _typed_value(row.get("o") or {})
        if prop is None or value is None:
            continue
        key = "@type" if prop.endswith("22-rdf-syntax-ns#type") else compact(prop)
        if key == "@type":
            node["@type"] = (
                value["@id"] if isinstance(value, dict) else value
            )
            continue
        node.setdefault(key, []).append(value)

    # `model_validate` rather than `**kwargs`: the aliases are `@context` and
    # `@graph`, which are not identifiers, and splatting a dict of mixed value
    # types past a typed signature is what mypy objects to.
    return V2GraphEntityResponse.model_validate(
        {"@context": load_generated_context(), "@graph": [node]},
    )


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------


@v2_router.get("/graph/provenance", response_model=V2GraphProvenanceResponse)
async def graph_provenance(
    _token: Annotated[str, Depends(verify_token)],
    subject: Annotated[str, Query(description="The entity IRI to explain")],
    prop: Annotated[
        str | None,
        Query(
            alias="property",
            description="Restrict to one property, compact or full IRI",
        ),
    ] = None,
) -> Any:
    """Where each *chosen* value for `subject` came from.

    Answers the question the whole four-layer model exists for: this canonical
    triple says X — which source said it, in which run, and by what rule was it
    picked over the alternatives.

    **Only chosen values appear.** A value with a single source is already
    attributed by the named graph it sits in, so the writer records nothing for
    it (the derived-only grain). An empty `records` list therefore means "no
    value here was contested", not "no provenance". `same_as` is separate
    because those are plain triples, not quoted-triple annotations.
    """
    store = _store()
    if store is None:
        return _no_store()
    try:
        subject_term = iri_term(subject)
    except UnusableIRIError as exc:
        return _bad_iri(exc)

    property_filter = ""
    if prop:
        try:
            property_filter = f"FILTER(?p = {iri_term(expand(prop))})"
        except UnusableIRIError as exc:
            return _bad_iri(exc)

    # The join the ontology documents, plus one hop: `prov:wasDerivedFrom`
    # names the ExtractionOutput, and the run is on the output in the meta
    # graph. A caller asking "which run said this" should not have to make two
    # requests to find out.
    rows = await store.select(
        f"""
        PREFIX pulse: <https://open-pulse.epfl.ch/ontology#>
        PREFIX prov: <http://www.w3.org/ns/prov#>
        SELECT ?p ?o ?output ?run ?kind ?count ?first ?last ?observed WHERE {{
          GRAPH <{PROV_GRAPH}> {{
            ?t <{WAS_DERIVED_FROM}> ?output .
            OPTIONAL {{ ?t <{OBSERVATION_KIND}> ?kind }}
            OPTIONAL {{ ?t <{OBSERVATION_COUNT}> ?count }}
            OPTIONAL {{ ?t <{FIRST_OBSERVED_ON}> ?first }}
            OPTIONAL {{ ?t <{LAST_CONFIRMED_ON}> ?last }}
            OPTIONAL {{ ?t <{OBSERVED_ON}> ?observed }}
          }}
          FILTER(isTRIPLE(?t))
          BIND(SUBJECT(?t) AS ?s)
          BIND(PREDICATE(?t) AS ?p)
          BIND(OBJECT(?t) AS ?o)
          FILTER(?s = {subject_term})
          {property_filter}
          OPTIONAL {{ GRAPH ?meta {{ ?output prov:wasGeneratedBy ?run }} }}
        }}
        """,
    )

    same_as_list = ", ".join(f"<{name}>" for name in SAME_AS_PROPERTIES)
    identity_rows = await store.select(
        f"""
        SELECT ?p ?other WHERE {{
          GRAPH <{PROV_GRAPH}> {{ {subject_term} ?p ?other }}
          FILTER(?p IN ({same_as_list}))
        }}
        """,
    )

    return V2GraphProvenanceResponse(
        subject=subject,
        records=[
            V2ProvenanceRecord(
                property=compact(str(_binding(row, "p"))),
                value=_binding(row, "o"),
                derived_from=_binding(row, "output"),
                run=_binding(row, "run"),
                observation_kind=_binding(row, "kind"),
                observation_count=(
                    int(count) if (count := _binding(row, "count")) else None
                ),
                first_observed_on=_binding(row, "first"),
                last_confirmed_on=_binding(row, "last"),
                observed_on=_binding(row, "observed"),
            )
            for row in rows
            if _binding(row, "p")
        ],
        same_as=[
            str(other)
            for row in identity_rows
            if (other := _binding(row, "other"))
        ],
    )
