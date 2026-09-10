"""Write the unifier's decisions into `graph:prov` as RDF-star annotations.

`PROVENANCE_ARCHITECTURE.md` phase 5. When the unifier chooses one value among
several, that value is a *derived* fact — not what any single source said, but
what was decided among what several said. This records the decision on the
triple itself:

    << <https://orcid.org/0000-...> schema:name "Jane Doe" >>
        prov:wasDerivedFrom <urn:pulse:output:run-b:github> ;
        pulse:observationKind "most-complete-source" ;
        pulse:observedOn      "2026-09-09T11:00:00"^^xsd:dateTime ;
        pulse:firstObservedOn "2026-09-01T09:00:00"^^xsd:dateTime ;
        pulse:lastConfirmedOn "2026-09-09T11:00:00"^^xsd:dateTime ;
        pulse:observationCount 4 .

The vocabulary and that exact shape come from the pinned ontology, not from
here — `ontology-definitions-provenance.ttl` documents each annotation property
with a worked `<< ?s ?p ?o >>` example and the query pattern to read it back.
Nothing in this module invents a term.

**Why this is hand-built SPARQL text.** rdflib 6.3.2 cannot serialise quoted
triples and pyshacl 0.28.1 cannot validate them, which is the recorded reason
RDF-star is store-only (§3.2 of `ONTOLOGY_V3_REQUIREMENTS.md`). So the writer
cannot go through `substrate.to_nquads`; it emits `INSERT DATA` through
`store.update`. Individual *terms* are still serialised by rdflib —
`URIRef.n3()` and `Literal.n3()` — because escaping a name that contains a
quote, a backslash or a newline is not something to hand-roll over data
extracted from arbitrary repositories.

**Derived-only.** An uncontested value needs no record: it is already
attributed by the named graph it sits in, and reifying it would multiply
storage for what the substrate already holds. Only `Selection.contested` ones
are written — which is also why the ontology's example kind `"single-source"`
never appears here.

**No `pulse:observationConfidence`.** The ontology declares it and it is left
empty on purpose. The only thing this pipeline could derive it from is the
selection rule, which `pulse:observationKind` already states — so a confidence
would be the same information dressed as a measurement. A real one needs
per-source reliability weights, which nothing here has.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.store.terms import (
    compact,
    datetime_term,
    expand,
    iri_term,
    literal_term,
    term,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from git_metadata_extractor.store.oxigraph import OxigraphStore
    from git_metadata_extractor.unify.merge import MergedEntity
    from git_metadata_extractor.unify.policy import MergePolicy

logger = logging.getLogger(__name__)

#: The provenance graph. Never SHACL-validated and never serialised to JSON-LD:
#: it holds quoted triples, which neither the pinned pyshacl nor the pinned
#: rdflib can process. That is why it is not one of the shape sets.
PROV_GRAPH = "urn:pulse:graph:prov"

PULSE = "https://open-pulse.epfl.ch/ontology#"
PROV = "http://www.w3.org/ns/prov#"

WAS_DERIVED_FROM = f"{PROV}wasDerivedFrom"
OBSERVATION_KIND = f"{PULSE}observationKind"
OBSERVED_ON = f"{PULSE}observedOn"
FIRST_OBSERVED_ON = f"{PULSE}firstObservedOn"
LAST_CONFIRMED_ON = f"{PULSE}lastConfirmedOn"
OBSERVATION_COUNT = f"{PULSE}observationCount"

#: The two `owl:sameAs` subproperties, which the ontology is explicit are
#: **plain** triples in `graph:prov` and not quoted-triple annotations.
#:
#: They also cannot live in `graph:canonical`, which is how they were emitted
#: until this module existed: `PersonShape` and `OrganizationShape` are
#: `sh:closed` and ignore only `( rdf:type owl:sameAs )`, so `pulse:samePersonAs`
#: — a *subproperty* of `owl:sameAs`, not the term itself — is rejected. The
#: canonical graph validated at 0 violations anyway, because the 120-repo corpus
#: never produced a rename and so never emitted one.
SAME_AS_PROPERTIES: tuple[str, ...] = (
    f"{PULSE}samePersonAs",
    f"{PULSE}sameOrganizationAs",
)

#: `INSERT DATA` statements are chunked so one enormous request cannot be
#: refused whole. Chosen for legibility of a failure, not for throughput.
INSERT_CHUNK = 200

_PREFIX_BLOCK = f"""PREFIX pulse: <{PULSE}>
PREFIX prov: <{PROV}>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>"""


@dataclass(frozen=True, slots=True)
class Annotation:
    """One derived canonical triple, and what the unifier decided about it."""

    subject: str
    prop: str
    value: Any
    #: Whether the value is a node reference rather than a literal. Determines
    #: the term form, and getting it wrong makes the quoted triple describe a
    #: different triple than the one in `graph:canonical` — so the annotation
    #: silently attaches to nothing.
    is_reference: bool
    #: The `pulse:ExtractionOutput` the winning value was read from. The
    #: ontology is specific that this is the output, not the run: an output is
    #: one platform's slice, which is the granularity that answers "which
    #: source said this".
    source_graph: str | None
    kind: str

    @property
    def key(self) -> tuple[str, str, str, bool]:
        return (self.subject, self.prop, str(self.value), self.is_reference)


@dataclass(frozen=True, slots=True)
class ExistingAnnotation:
    """The history already in the store for one quoted triple."""

    count: int
    first_observed_on: str | None


@dataclass(slots=True)
class ProvenanceReport:
    inserted: int = 0
    reconfirmed: int = 0
    same_as: int = 0
    pruned: bool = False
    statements: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.inserted} new annotations, {self.reconfirmed} reconfirmed, "
            f"{self.same_as} sameAs edges, pruned={self.pruned}"
        )


# --------------------------------------------------------------------------
# what to write
# --------------------------------------------------------------------------


def annotations_for(entities: Iterable[MergedEntity]) -> list[Annotation]:
    """Every contested selection, as an annotation.

    Contested only — see the module docstring on the derived-only grain.
    """
    from git_metadata_extractor.unify.merge import (  # noqa: PLC0415
        contested_selections,
    )

    items = list(entities)
    by_reference = _reference_properties(items)
    return [
        Annotation(
            subject=selection.subject,
            prop=selection.prop,
            value=selection.winner,
            is_reference=(selection.subject, selection.prop) in by_reference,
            source_graph=selection.source_graph,
            kind=selection.rule,
        )
        for selection in contested_selections(items)
    ]


def _reference_properties(entities: Sequence[MergedEntity]) -> set[tuple[str, str]]:
    """`(subject, property)` pairs whose canonical value is a node reference.

    Read off the merged entity rather than guessed from the value: `merge`
    already decided the form when it rebuilt the node, and the annotation has
    to quote the triple exactly as `graph:canonical` holds it.
    """
    out: set[tuple[str, str]] = set()
    for entity in entities:
        for prop, value in entity.properties.items():
            items = value if isinstance(value, list) else [value]
            if any(isinstance(item, dict) and item.get("@id") for item in items):
                out.add((entity.iri, prop))
    return out


def same_as_edges(
    entities: Iterable[MergedEntity],
    policy: MergePolicy,
) -> list[tuple[str, str, str]]:
    """`(canonical, predicate, alias)` for every entity the unifier renamed."""
    out: list[tuple[str, str, str]] = []
    for entity in entities:
        resolver = policy.resolvers.get(entity.entity_type)
        if resolver is None or not resolver.same_as:
            continue
        predicate = resolver.same_as.replace("pulse:", PULSE)
        out.extend((entity.iri, predicate, alias) for alias in entity.aliases)
    return out


# --------------------------------------------------------------------------
# term serialisation
# --------------------------------------------------------------------------


#: Term serialisation lives in `store/terms.py`, shared with the query API.
#: It is the one place an injection can happen — SPARQL over HTTP has no
#: prepared statements — so it is written once and tested once rather than
#: inline at each call site.
_iri = iri_term
_literal = literal_term
_term = term
_timestamp = datetime_term


def quoted_triple(annotation: Annotation) -> str:
    subject = _iri(annotation.subject)
    predicate = _iri(_expand(annotation.prop))
    obj = _term(annotation.value, is_reference=annotation.is_reference)
    return f"<< {subject} {predicate} {obj} >>"


#: Shared with the query API and the unifier, from `store/terms.py`. There were
#: three copies of a prefix table across this package before it moved; a second
#: copy of one is how a context ends up unable to expand its own models' terms
#: (§3c of the handoff).
_expand = expand


# --------------------------------------------------------------------------
# reading the history back
# --------------------------------------------------------------------------


async def read_existing(store: OxigraphStore) -> dict[tuple[str, str, str, bool], ExistingAnnotation]:
    """`observationCount` and `firstObservedOn` already recorded, by triple.

    Read before writing because both are history: `observationCount` is a
    running total across runs and `firstObservedOn` is by definition the
    earliest. Rewriting the graph without carrying them forward would reset the
    counter to 1 on every pass and make the field a lie.
    """
    rows = await store.select(
        f"""{_PREFIX_BLOCK}
        SELECT ?s ?p ?o ?count ?first WHERE {{
          GRAPH <{PROV_GRAPH}> {{
            ?t pulse:observationCount ?count .
            OPTIONAL {{ ?t pulse:firstObservedOn ?first }}
          }}
          FILTER(isTRIPLE(?t))
          BIND(SUBJECT(?t) AS ?s)
          BIND(PREDICATE(?t) AS ?p)
          BIND(OBJECT(?t) AS ?o)
        }}
        """,
    )
    out: dict[tuple[str, str, str, bool], ExistingAnnotation] = {}
    for row in rows:
        subject, prop, obj = row.get("s"), row.get("p"), row.get("o")
        if not (subject and prop and obj):
            continue
        key = (
            str(subject["value"]),
            _compact(str(prop["value"])),
            str(obj["value"]),
            obj.get("type") == "uri",
        )
        first = row.get("first") or {}
        out[key] = ExistingAnnotation(
            count=int(str((row.get("count") or {}).get("value") or 0)),
            first_observed_on=str(first["value"]) if first.get("value") else None,
        )
    return out


_compact = compact


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


def _annotation_statement(
    annotation: Annotation,
    *,
    now: str,
    existing: ExistingAnnotation | None,
) -> str:
    triple = quoted_triple(annotation)
    lines = [f"    {triple} <{OBSERVATION_KIND}> {_literal(annotation.kind)} ;"]
    if annotation.source_graph:
        lines.append(f"        <{WAS_DERIVED_FROM}> {_iri(annotation.source_graph)} ;")
    first = (existing.first_observed_on if existing else None) or now
    count = (existing.count if existing else 0) + 1
    lines.extend(
        [
            f"        <{OBSERVED_ON}> {_timestamp(now)} ;",
            f"        <{FIRST_OBSERVED_ON}> {_timestamp(first)} ;",
            f"        <{LAST_CONFIRMED_ON}> {_timestamp(now)} ;",
            f"        <{OBSERVATION_COUNT}> {_literal(count)} .",
        ],
    )
    return "\n".join(lines)


def build_statements(
    annotations: Sequence[Annotation],
    same_as: Sequence[tuple[str, str, str]],
    existing: Mapping[tuple[str, str, str, bool], ExistingAnnotation],
    *,
    now: str,
) -> list[str]:
    """The SPARQL Update statements for one provenance pass, in order.

    A clear-then-write rather than a per-triple upsert, and the reason is the
    round-trip count: a corpus-scale pass has hundreds of annotations, and a
    `DELETE/INSERT WHERE` each would be hundreds of requests. The history that
    makes an upsert an upsert — `observationCount`, `firstObservedOn` — is
    carried forward in Python from `read_existing` instead, so the result is
    the same and the cost is one read plus a bounded number of writes.

    The consequence to know: this assumes unification is a batch job, not a
    concurrent one. Two passes racing would lose one's counter increments. So
    does the `DROP` in `write_canonical`, for the same reason.
    """
    statements: list[str] = []

    # Clear what this pass is about to restate. Scoped by `isTRIPLE` and by
    # predicate rather than dropping the graph, so anything else a future phase
    # puts in `graph:prov` is left alone.
    statements.append(
        f"""{_PREFIX_BLOCK}
DELETE {{ GRAPH <{PROV_GRAPH}> {{ ?t ?p ?o }} }}
WHERE {{ GRAPH <{PROV_GRAPH}> {{ ?t ?p ?o }} FILTER(isTRIPLE(?t)) }}""",
    )
    same_as_list = ", ".join(f"<{prop}>" for prop in SAME_AS_PROPERTIES)
    statements.append(
        f"""{_PREFIX_BLOCK}
DELETE {{ GRAPH <{PROV_GRAPH}> {{ ?s ?p ?o }} }}
WHERE {{ GRAPH <{PROV_GRAPH}> {{ ?s ?p ?o }} FILTER(?p IN ({same_as_list})) }}""",
    )

    for start in range(0, len(annotations), INSERT_CHUNK):
        chunk = annotations[start : start + INSERT_CHUNK]
        body = "\n".join(
            _annotation_statement(item, now=now, existing=existing.get(item.key))
            for item in chunk
        )
        statements.append(
            f"{_PREFIX_BLOCK}\nINSERT DATA {{\n  GRAPH <{PROV_GRAPH}> {{\n{body}\n  }}\n}}",
        )

    for start in range(0, len(same_as), INSERT_CHUNK):
        edges = same_as[start : start + INSERT_CHUNK]
        body = "\n".join(
            f"    {_iri(subject)} <{predicate}> {_iri(obj)} ."
            for subject, predicate, obj in edges
        )
        statements.append(
            f"{_PREFIX_BLOCK}\nINSERT DATA {{\n  GRAPH <{PROV_GRAPH}> {{\n{body}\n  }}\n}}",
        )
    return statements


def prune_statement(canonical_graph: str) -> str:
    """Drop annotations whose quoted triple is no longer canonical.

    `graph:canonical` is replaced on every pass, so a value that stops winning
    leaves an annotation describing a triple that is not there — and the
    ontology's own query pattern joins `graph:canonical` to `graph:prov`, so
    such a record can never be reached and only misleads anyone reading the
    graph directly.

    Pruning discards the observation history of superseded values. That is the
    "cheap corner" the architecture's cost profile chooses — derived-only,
    upsert, no append — as against an `Observation`-per-observation audit
    trail. Worth knowing if a value flaps: its counter restarts.
    """
    return f"""{_PREFIX_BLOCK}
DELETE {{ GRAPH <{PROV_GRAPH}> {{ ?t ?ap ?av }} }}
WHERE {{
  GRAPH <{PROV_GRAPH}> {{ ?t ?ap ?av }}
  FILTER(isTRIPLE(?t))
  FILTER NOT EXISTS {{
    GRAPH <{canonical_graph}> {{ ?s ?p ?o . FILTER(?t = TRIPLE(?s, ?p, ?o)) }}
  }}
}}"""


async def write_provenance(
    store: OxigraphStore,
    entities: Iterable[MergedEntity],
    *,
    policy: MergePolicy,
    canonical_graph: str,
    now: datetime | None = None,
) -> ProvenanceReport:
    """Record this pass's derived values and identity links in `graph:prov`."""
    items = list(entities)
    moment = (now or datetime.now(timezone.utc)).isoformat()
    annotations = annotations_for(items)
    same_as = same_as_edges(items, policy)
    existing = await read_existing(store)

    statements = build_statements(annotations, same_as, existing, now=moment)
    for statement in statements:
        await store.update(statement)
    await store.update(prune_statement(canonical_graph))

    report = ProvenanceReport(
        inserted=sum(1 for item in annotations if item.key not in existing),
        reconfirmed=sum(1 for item in annotations if item.key in existing),
        same_as=len(same_as),
        pruned=True,
        statements=statements,
    )
    logger.info("provenance: %s", report.summary())
    return report


__all__ = [
    "FIRST_OBSERVED_ON",
    "INSERT_CHUNK",
    "LAST_CONFIRMED_ON",
    "OBSERVATION_COUNT",
    "OBSERVATION_KIND",
    "OBSERVED_ON",
    "PROV_GRAPH",
    "SAME_AS_PROPERTIES",
    "WAS_DERIVED_FROM",
    "Annotation",
    "ExistingAnnotation",
    "ProvenanceReport",
    "annotations_for",
    "build_statements",
    "prune_statement",
    "quoted_triple",
    "read_existing",
    "same_as_edges",
    "write_provenance",
]
