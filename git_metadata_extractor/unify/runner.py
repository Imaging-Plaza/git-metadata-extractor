"""Read the accumulated substrate, unify it, write `graph:canonical`.

The orchestration around `cluster` and `merge`. Everything here is I/O against
the store; the decisions live in `unify.policy` and the machinery in the other
two modules, so this file should stay boring.

**Why the substrate is read as quads and merged in Python** rather than unified
by a single SPARQL `CONSTRUCT`. The clustering is transitive — union-find over
several match keys with a veto from the stronger ones — and SPARQL has no
transitive closure over arbitrary joins. A `CONSTRUCT` could express one match
key at a time, which is exactly the version that merges two researchers who
share a name. Reading the graphs out, deciding, and writing back is the honest
shape, and it is also what lets a decision carry a `Selection` explaining
itself.

**Why the canonical graph is replaced, not merged into.** Unification is a pure
function of the substrate: given the same accumulated runs it must produce the
same canonical graph, or the store drifts in ways nothing can audit. So the
writer `DROP`s and rewrites `graph:canonical`, and the substrate — which is
append-only and never rewritten — remains the only durable record. Losing
`graph:canonical` costs one re-run of this function; losing the substrate loses
data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.store.terms import compact
from git_metadata_extractor.unify.cluster import (
    Record,
    cluster_records,
    group_by_type,
)
from git_metadata_extractor.unify.merge import contested_selections, merge_cluster
from git_metadata_extractor.unify.policy import default_policy
from git_metadata_extractor.unify.remap import (
    alias_map,
    dangling_references,
    remap_entities,
    stale_references,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from git_metadata_extractor.store.oxigraph import OxigraphStore
    from git_metadata_extractor.unify.merge import MergedEntity, Selection
    from git_metadata_extractor.unify.policy import MergePolicy

logger = logging.getLogger(__name__)

#: Where the unifier's output lives. A named graph, so a canonical query never
#: pays for the substrate and provenance can sit in a third graph of its own.
CANONICAL_GRAPH = "urn:pulse:graph:canonical"

#: Substrate graphs are `urn:pulse:output:{run}:{platform}`; the run's meta
#: graph is `urn:pulse:run:{run}#meta`. Only the former hold entities.
_SUBSTRATE_PREFIX = "urn:pulse:output:"


@dataclass(slots=True)
class UnifyReport:
    """What one unification pass did, for logs and for a caller to assert on."""

    records_read: int = 0
    graphs_read: int = 0
    clusters: int = 0
    entities_written: int = 0
    aliases_written: int = 0
    #: Clusters that drew records from more than one named graph — the
    #: cross-run merges, which is the number this whole phase exists to move.
    cross_graph_clusters: int = 0
    #: Entities the unifier renamed — the count of `old -> canonical` mappings
    #: that had to be pushed through every reference.
    remapped_references: int = 0
    contested: list[Selection] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)
    #: `entity -> references with no entity behind them`. Reported, not pruned:
    #: the canonical graph legitimately points at nodes the unifier does not
    #: own (profiles, SPDX licences, Wikidata disciplines), so only a caller
    #: can tell a real break from an external reference.
    dangling: dict[str, list[str]] = field(default_factory=dict)
    #: References still pointing at a renamed IRI after the remap pass. Unlike
    #: `dangling`, this must be empty — every entry is broken by construction.
    stale: dict[str, list[str]] = field(default_factory=dict)
    #: What the provenance pass wrote, when it ran.
    provenance: Any = None
    #: `property -> how many entities dropped it` because the closed canonical
    #: shape has no slot for it. Not a loss (the substrate keeps them), but a
    #: property dropped for *every* entity of a type usually means the raw
    #: projection and the canonical shapes disagree.
    dropped_properties: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        types = " ".join(
            f"{name.split(':')[-1]}={count}" for name, count in sorted(self.by_type.items())
        )
        return (
            f"read {self.records_read} records from {self.graphs_read} graphs -> "
            f"{self.clusters} clusters ({self.cross_graph_clusters} cross-run), "
            f"wrote {self.entities_written} entities + {self.aliases_written} aliases "
            f"[{types}] renamed={self.remapped_references} "
            f"contested={len(self.contested)} dangling={len(self.dangling)}"
            + (f" STALE={len(self.stale)}" if self.stale else "")
            + (f" dropped={sum(self.dropped_properties.values())}" if self.dropped_properties else "")
            + (f" prov[{self.provenance.summary()}]" if self.provenance else "")
        )


async def read_substrate(store: OxigraphStore) -> list[Record]:
    """Every entity in every substrate slice, as `Record`s.

    One query rather than one per graph: the store may hold thousands of runs,
    and a round trip each would dominate. `?g` comes back on every row, which
    is what preserves the provenance handle through clustering.
    """
    rows = await store.select(
        f"""
        SELECT ?g ?s ?type ?p ?o WHERE {{
          GRAPH ?g {{
            ?s a ?type .
            ?s ?p ?o .
          }}
          FILTER(STRSTARTS(STR(?g), "{_SUBSTRATE_PREFIX}"))
        }}
        """,
    )
    by_key: dict[tuple[str, str], Record] = {}
    for row in rows:
        graph, subject = _binding(row, "g"), _binding(row, "s")
        entity_type, prop = _binding(row, "type"), _binding(row, "p")
        if not (graph and subject and entity_type and prop):
            continue
        if prop.endswith("22-rdf-syntax-ns#type"):
            continue
        key = (graph, subject)
        record = by_key.get(key)
        if record is None:
            record = Record(
                iri=subject,
                entity_type=_compact(entity_type),
                graph=graph,
            )
            by_key[key] = record
        value = _object_value(row.get("o") or {})
        if value is None:
            continue
        record.properties.setdefault(_compact(prop), []).append(value)
    return list(by_key.values())


def _binding(row: Mapping[str, Any], name: str) -> str | None:
    entry = row.get(name)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    return str(value) if value is not None else None


def _object_value(entry: Mapping[str, Any]) -> Any:
    """A SPARQL object binding as the JSON-LD shape the merger expects."""
    value = entry.get("value")
    if value is None:
        return None
    if entry.get("type") == "uri":
        return {"@id": str(value)}
    datatype = str(entry.get("datatype") or "")
    if datatype.endswith(("#integer", "#int", "#long")):
        try:
            return int(value)
        except ValueError:
            return str(value)
    return str(value)


#: Shared with the query API, and tested against the reader's table. It lived
#: here with a comment claiming a subset test existed; it did not — the same
#: cited-but-missing test `raw_projection` had. Moving it to `store/terms.py`
#: put it somewhere both consumers can reach and made the test real.
_compact = compact


def unify_records(
    records: Iterable[Record],
    *,
    policy: MergePolicy | None = None,
) -> tuple[list[MergedEntity], UnifyReport]:
    """Cluster and merge, with no I/O. The whole decision path, testable."""
    active = policy or default_policy()
    items = list(records)
    report = UnifyReport(
        records_read=len(items),
        graphs_read=len({record.graph for record in items}),
    )

    merged: list[MergedEntity] = []
    for entity_type, group in sorted(group_by_type(items).items()):
        resolver = active.resolvers.get(entity_type)
        if resolver is None:
            # A type with no resolver is not an error: profiles, deposits and
            # extraction outputs are carried by their subject rather than
            # unified in their own right. Logged so a *new* type shows up as a
            # gap instead of vanishing.
            logger.debug("unify: no resolver for %s (%d records)", entity_type, len(group))
            continue
        clusters = cluster_records(group, resolver)
        report.clusters += len(clusters)
        report.by_type[entity_type] = len(clusters)
        for cluster in clusters:
            if len(cluster.graphs) > 1:
                report.cross_graph_clusters += 1
            merged.append(merge_cluster(cluster, active))

    # A renamed entity leaves every edge that pointed at its old IRI dangling,
    # and `graph:canonical` is validated by closed shapes with `sh:class`
    # constraints — so the remap is not a tidy-up, it is what keeps the output
    # valid. `prune_dangling_refs` is a pipeline stage and does not run here.
    mapping = alias_map(merged)
    merged = remap_entities(merged, mapping)
    stale = stale_references(merged, mapping)
    if stale:
        # An invariant, not a diagnostic: every IRI in `mapping` was an entity
        # in this batch and is now something else, so a surviving reference to
        # one is broken by construction. Logged at error rather than raised —
        # the canonical graph is still better than no canonical graph — but it
        # means the remap has a hole.
        logger.error(
            "unify: %d entities still reference a renamed IRI after remap: %s",
            len(stale),
            sorted(stale)[:5],
        )
    report.stale = stale

    report.entities_written = len(merged)
    report.aliases_written = sum(len(entity.aliases) for entity in merged)
    report.remapped_references = len(mapping)
    report.contested = contested_selections(merged)
    report.dangling = dangling_references(merged)
    for entity in merged:
        for prop in entity.dropped:
            report.dropped_properties[prop] = report.dropped_properties.get(prop, 0) + 1
    return merged, report


def canonical_document(
    entities: Iterable[MergedEntity],
    *,
    context: Mapping[str, Any],
    policy: MergePolicy | None = None,
) -> dict[str, Any]:
    """The merged entities as one named-graph JSON-LD document."""
    _ = policy or default_policy()
    nodes = [entity.as_jsonld() for entity in entities]
    return {
        "@context": dict(context),
        "@graph": [{"@id": CANONICAL_GRAPH, "@graph": nodes}],
    }


async def write_canonical(
    store: OxigraphStore,
    document: Mapping[str, Any],
    *,
    enforce: bool = True,
) -> int:
    """Replace `graph:canonical` with `document`. Returns quads written.

    `enforce` runs the closed-shape gate **before** anything is dropped —
    `PROVENANCE_ARCHITECTURE.md` phase 6's "fail the canonical build on
    violation". The ordering is the whole point: validate first and a bad pass
    leaves the previous canonical graph in place, still valid and still
    answering queries. Validate after the `DROP` and a bad pass leaves nothing.

    This is also the sufficiency check. Each layer passing its own shapes does
    not mean the substrate holds what canonical needs — the substrate was
    119/119 raw-conformant while missing two required things (§3j) — and the
    only way to find that out is to build canonical from the substrate and
    validate the result, which is exactly this call.
    """
    from git_metadata_extractor.pipeline.stages.substrate import (  # noqa: PLC0415
        to_nquads,
    )
    from git_metadata_extractor.validation import enforce_canonical  # noqa: PLC0415

    if enforce:
        result = enforce_canonical(document)
        logger.info("canonical gate: %s", result.summary())

    # DROP first: unification is a pure function of the substrate, so a stale
    # entity that no longer follows from it must not survive. SILENT because a
    # first run has no graph to drop.
    await store.update(f"DROP SILENT GRAPH <{CANONICAL_GRAPH}>")
    nquads = to_nquads(document)
    await store.load_nquads(nquads)
    return sum(1 for line in nquads.splitlines() if line.strip())


async def unify_store(  # noqa: PLR0913 — one keyword per independent stage
    store: OxigraphStore,
    *,
    context: Mapping[str, Any],
    policy: MergePolicy | None = None,
    write: bool = True,
    provenance: bool = True,
    enforce: bool = True,
) -> UnifyReport:
    """Read, unify, and (unless `write=False`) replace `graph:canonical`.

    `write=False` is the read-only mode: it reports what unification *would*
    do without touching the store, which is what you want the first time it
    runs over a store you care about. It implies no provenance write either —
    `graph:prov` annotates the canonical triples, so recording decisions about
    a graph that was not written would leave the two inconsistent.
    """
    from git_metadata_extractor.unify.provenance import (  # noqa: PLC0415
        write_provenance,
    )

    active = policy or default_policy()
    records = await read_substrate(store)
    merged, report = unify_records(records, policy=active)
    if not write:
        logger.info("unify: %s (dry run)", report.summary())
        return report

    document = canonical_document(merged, context=context, policy=active)
    report.entities_written = len(document["@graph"][0]["@graph"])
    await write_canonical(store, document, enforce=enforce)

    # After the canonical write, never before: the prune step joins against
    # `graph:canonical` to drop annotations for values that no longer win, so
    # it has to see the graph this pass produced.
    if provenance:
        report.provenance = await write_provenance(
            store,
            merged,
            policy=active,
            canonical_graph=CANONICAL_GRAPH,
        )
    logger.info("unify: %s", report.summary())
    return report


__all__ = [
    "CANONICAL_GRAPH",
    "UnifyReport",
    "canonical_document",
    "read_substrate",
    "unify_records",
    "unify_store",
    "write_canonical",
]
