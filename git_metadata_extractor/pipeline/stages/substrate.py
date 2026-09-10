"""Group the raw layer into named graphs — the substrate the store accumulates.

The third of the four layers in `PROVENANCE_ARCHITECTURE.md`, and the first
one that is not a single `@graph`. `raw_projection` says *what* a source
asserted; this module says *where those assertions live*, which is the whole
mechanism by which a canonical triple can later be traced back to the source
that produced it.

**The unit of grouping is not a source, it is one platform's slice of one
run.** That comes from the shapes rather than from a convention invented here:
`pulse:partOfRun` points at a `pulse:ExtractionOutput`, and
`ExtractionOutputShape` carries `prov:wasGeneratedBy` → `pulse:ExtractionRun`
plus `pulse:platform`. So `raw_projection.extraction_output_iri` already mints
exactly the IRI a named graph should carry, and this module only has to route
each node to it. That answers the architecture document's open gap 3.

**Why a nested-`@graph` JSON-LD document rather than a dict of graphs.**
JSON-LD 1.1 encodes named graphs natively — an entry with an `@id` *and* an
`@graph` is a named graph — so the document this builds parses straight into an
`rdflib.Dataset` and serialises to N-Quads with no bespoke code on either side.
An invented `{"@graphs": {...}}` envelope would need a translator at every
boundary and would not be RDF.

The substrate is **append-only by construction**: every graph IRI embeds the
run id, so a second run over the same repository writes new graphs beside the
old ones rather than rewriting them. That is what "raw assertions, never
rewritten" means in practice, and it is what lets the unifier see the history
of what each source said.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from rdflib import Dataset

from git_metadata_extractor.pipeline.stages.extraction_run import run_iri
from git_metadata_extractor.pipeline.stages.raw_projection import project_raw

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

#: Suffix for the graph describing the extraction itself. A fragment on the run
#: IRI rather than a fourth `urn:` namespace: it *is* metadata about that run,
#: and keeping the association in the IRI means a store holding many runs can
#: find a run's meta graph without an index.
META_GRAPH_SUFFIX = "#meta"


def meta_graph_iri(run_id: str) -> str:
    """The graph holding the run descriptor and its extraction outputs."""
    return f"{run_iri(run_id)}{META_GRAPH_SUFFIX}"


#: How a **dependent** node finds its subject — one whose shape declares no
#: `pulse:partOfRun`, so the named graph it sits in is its only attribution.
#:
#: Two directions, because the shapes point two different ways. A profile names
#: its subject (`pulse:profileOf`), so it can be resolved by reading the node.
#: A membership is named *by* its subject (`schema:Person org:hasMembership`),
#: so it needs a reverse index. Getting either wrong is not a validation
#: failure — `RawMembershipShape` requires nothing, so a membership stranded in
#: the meta graph conforms perfectly while claiming that "X was employed at Y"
#: is a fact about the extraction rather than about X.
_NAMES_SUBJECT: tuple[str, ...] = (
    "pulse:profileOf",
    "pulse:organizationProfileOf",
    # A `pulse:Deposit` is the platform record an article was found in, so it
    # belongs with the article. Added when the deposit moved into the raw
    # projection: without it every deposit stranded in the meta graph, and the
    # only visible symptom was that the article's `pulse:hasDeposit` pointed at
    # a node in a different graph — which SHACL, validating the union, cannot
    # see at all.
    "pulse:depositOf",
    # A `pulse:Contribution` names *two* subjects, and the repository is the
    # right one: it is the node that carries a `pulse:platform`, so it decides
    # which slice reported the commits. The person may be ORCID-identified and
    # hold no platform at all, which would strand the contribution.
    "pulse:contributionTo",
    "schema:author",
)
_NAMED_BY_SUBJECT: tuple[str, ...] = ("org:hasMembership", "pulse:hasContribution")


def group_by_output(
    raw_nodes: Iterable[Mapping[str, Any]],
    *,
    meta_graph: str,
) -> dict[str, list[dict[str, Any]]]:
    """Assign every raw node to the named graph it belongs in.

    Three routes, in order:

    1. An entity goes to the `pulse:ExtractionOutput` its `pulse:partOfRun`
       names. That is the anchor `raw_projection` stamped.
    2. A **dependent node goes with its subject** — a profile with the entity
       it describes, a membership with the person who holds it. See
       `_NAMES_SUBJECT` / `_NAMED_BY_SUBJECT`.
    3. Everything else goes to the meta graph: the outputs themselves, the run,
       the software agent. These describe the extraction rather than any
       entity.

    Route 3 is a floor, not a home. An *entity* landing there is a defect in
    the anchoring, not a category of node, and every such defect found so far
    was invisible to SHACL: a ROR-identified organization arriving for want of
    a platform (ontology patch 05), an article whose DOI id names a resolver
    rather than the repository that holds the record, a membership with no
    anchor slot at all. The stage logs the residue for exactly that reason.
    """
    nodes = [node for node in raw_nodes if isinstance(node, dict) and node.get("@id")]
    outputs = {
        str(node["@id"])
        for node in nodes
        if str(node.get("@type")) == "pulse:ExtractionOutput"
    }

    anchored: dict[str, str] = {}
    for node in nodes:
        part_of = node.get("pulse:partOfRun")
        if isinstance(part_of, dict) and str(part_of.get("@id")) in outputs:
            anchored[str(node["@id"])] = str(part_of["@id"])

    claimed = _claimed_dependents(nodes, anchored)

    by_graph: dict[str, list[dict[str, Any]]] = {name: [] for name in sorted(outputs)}
    by_graph[meta_graph] = []

    for node in nodes:
        node_id = str(node["@id"])
        if node_id in outputs:
            by_graph[meta_graph].append(node)
            continue
        target = (
            anchored.get(node_id)
            or _named_subject_graph(node, anchored)
            or claimed.get(node_id)
        )
        by_graph.setdefault(target or meta_graph, []).append(node)
    return by_graph


def _named_subject_graph(
    node: Mapping[str, Any],
    anchored: Mapping[str, str],
) -> str | None:
    """The graph of the subject this node names, if it names one."""
    for key in _NAMES_SUBJECT:
        subject = node.get(key)
        if isinstance(subject, dict) and subject.get("@id"):
            return anchored.get(str(subject["@id"]))
    return None


def _claimed_dependents(
    nodes: Iterable[Mapping[str, Any]],
    anchored: Mapping[str, str],
) -> dict[str, str]:
    """Reverse index: dependent id -> the graph of the entity that claims it."""
    claimed: dict[str, str] = {}
    for node in nodes:
        graph = anchored.get(str(node.get("@id")))
        if graph is None:
            continue
        for key in _NAMED_BY_SUBJECT:
            value = node.get(key)
            for ref in value if isinstance(value, list) else [value]:
                if isinstance(ref, dict) and ref.get("@id"):
                    claimed.setdefault(str(ref["@id"]), graph)
    return claimed


def build_substrate(
    nodes: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    context: Mapping[str, Any],
    run_nodes: Iterable[Mapping[str, Any]] = (),
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Project `nodes` into the raw shapes and group them into named graphs.

    `nodes` is the flat v2 intermediate `build_jsonld_output` produces — the
    same input `canonical_projection` consumes. Both layers are projections of
    it; neither is derived from the other.

    Returns a JSON-LD document whose top-level `@graph` holds one named-graph
    entry per `pulse:ExtractionOutput`, plus the meta graph. Empty graphs are
    dropped: a run that found nothing on a platform should not assert that it
    has an empty slice there.
    """
    raw = project_raw(
        nodes,
        run_id=run_id,
        run_nodes=run_nodes,
        generated_at=generated_at,
    )
    by_graph = group_by_output(raw["@graph"], meta_graph=meta_graph_iri(run_id))
    return {
        "@context": dict(context),
        "@graph": [
            {"@id": name, "@graph": graph_nodes}
            for name, graph_nodes in by_graph.items()
            if graph_nodes
        ],
    }


def named_graph_sizes(document: Mapping[str, Any]) -> dict[str, int]:
    """Node count per named graph — for logs and for the response stats."""
    entries = document.get("@graph")
    if not isinstance(entries, list):
        return {}
    return {
        str(entry["@id"]): len(entry.get("@graph") or [])
        for entry in entries
        if isinstance(entry, dict) and entry.get("@id")
    }


def to_nquads(document: Mapping[str, Any]) -> str:
    """Serialise the substrate document as N-Quads, for loading into a store.

    N-Quads rather than TriG because it is the format every quad store accepts
    on a bulk load and the one with no parser ambiguity — line-oriented, no
    prefixes, no nesting. TriG is for reading (see
    `scripts/v2/emit_proof_artifacts.py`); this is for writing.
    """
    dataset = Dataset()
    dataset.parse(data=json.dumps(document), format="json-ld")
    return str(dataset.serialize(format="nquads"))


__all__ = [
    "META_GRAPH_SUFFIX",
    "build_substrate",
    "group_by_output",
    "meta_graph_iri",
    "named_graph_sizes",
    "to_nquads",
]
