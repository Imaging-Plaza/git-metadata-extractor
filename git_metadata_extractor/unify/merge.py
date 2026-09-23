"""Turn a cluster into one canonical node, and say why each value won.

The type-agnostic other half. Three jobs, in order:

1. **Name it.** Promote the cluster's identity to the best available global id —
   `ORCID → ROR → DOI → else the first source IRI`. Same priority
   `canonicalization/id_resolution.py` applies per request, deliberately: under
   the decision recorded for phase 4, extraction keeps its own resolution, so
   the two agree wherever they see the same evidence and diverge only where the
   unifier saw more. `owl:sameAs` carries the divergence.
2. **Merge each property** by its `Disposition` — union, select, or drop.
3. **Record the selections.** A chosen value is a *derived* fact: it is not
   what any one source said, it is what the unifier decided among what several
   said. `PROVENANCE_ARCHITECTURE.md` requires that decision be recoverable,
   so every SELECT emits a `Selection` naming the winner, the losers and the
   rule. Phase 5 writes those into `graph:prov`; this module only has to make
   them, which is why they come back beside the node instead of inside it.

**Unions need no record.** A union asserts everything every source asserted, so
each triple is still attributed by the named graph it came from — the "derived
only" grain the cost profile specifies. Reifying them would multiply storage
for information the substrate already holds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.unify.policy import (
    Disposition,
    canonical_properties_for,
    single_valued_for,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from git_metadata_extractor.unify.cluster import Cluster, Record
    from git_metadata_extractor.unify.policy import MergePolicy, TypeResolver

#: How a winner was chosen, for the provenance record. Ordered weakest-last so
#: a reader can tell a real decision from a formality.
logger = logging.getLogger(__name__)

RULE_ONLY_CANDIDATE = "only-candidate"
RULE_MOST_COMPLETE_SOURCE = "most-complete-source"
RULE_FIRST_STABLE = "first-stable"


@dataclass(frozen=True, slots=True)
class Selection:
    """One contested property, resolved. The unit phase 5 will reify."""

    subject: str
    prop: str
    winner: Any
    losers: tuple[Any, ...]
    rule: str
    #: The named graph the winning value came from — the link back to a source.
    source_graph: str | None = None

    @property
    def contested(self) -> bool:
        """Whether anything was actually decided, as opposed to copied."""
        return bool(self.losers)


@dataclass(slots=True)
class MergedEntity:
    """A canonical node plus everything needed to explain and link it."""

    iri: str
    entity_type: str
    properties: dict[str, Any] = field(default_factory=dict)
    #: Source IRIs that are not the canonical one. Each becomes an
    #: `owl:sameAs` subproperty edge, so an id this run re-identified stays
    #: followable — the bridge the id-migration decision requires.
    aliases: tuple[str, ...] = ()
    selections: tuple[Selection, ...] = ()
    graphs: tuple[str, ...] = ()
    #: Substrate properties the closed canonical shape has no slot for. Not
    #: lost — the substrate keeps them — but worth surfacing, because a
    #: property being dropped for every entity of a type usually means the
    #: projection and the shapes disagree rather than that the data is odd.
    dropped: tuple[str, ...] = ()

    def as_jsonld(self) -> dict[str, Any]:
        """The canonical node. **Without** the identity-link edges.

        Those go to `graph:prov` as plain triples — which is what the ontology
        says ("Plain triple in graph:prov, not a quoted-triple annotation") and
        also what the shapes require: `PersonShape` and `OrganizationShape` are
        `sh:closed` and ignore only `( rdf:type owl:sameAs )`, so
        `pulse:samePersonAs` is rejected — it is a subproperty of
        `skos:exactMatch` (patch 09), and even under the `owl:sameAs` reading it
        superseded, a *subproperty* is not the ignored term itself.

        This method emitted them until `unify/provenance.py` existed, and the
        canonical graph still validated at 0 violations, because the 120-repo
        corpus never produced a rename and so never emitted one. A latent
        closed-shape violation waiting for the first cross-run identity merge.
        """
        return {"@id": self.iri, "@type": self.entity_type, **self.properties}


def _identifier_count(record: Record, resolver: TypeResolver) -> int:
    """How many of the type's match keys this record actually carries.

    The prototype in `dev/.../deduplication/` scored completeness this way and
    its docstring is worth preserving: *number* of identifiers first, priority
    weight only as a tie-break, so a record with a GitHub profile and an
    Infoscience id beats one with an ORCID alone. Recovered from bytecode —
    the sources are gone.
    """
    return sum(1 for key in resolver.match_keys if record.values(key))


def _promoted_iri(cluster: Cluster, resolver: TypeResolver) -> tuple[str, tuple[str, ...]]:
    """(canonical IRI, the other source IRIs) for this cluster."""
    for prop in resolver.id_priority:
        for value in cluster.values(prop):
            promoted = _iri_for(prop, value)
            if promoted:
                aliases = tuple(iri for iri in cluster.iris if iri != promoted)
                return promoted, aliases
    # Nothing to promote to. Prefer a source IRI that is not a `urn:` stub:
    # a `urn:pulse:` id is what extraction falls back to when it knows no
    # global identifier, so any real URL in the cluster is a better name.
    ordered = sorted(cluster.iris, key=lambda iri: (iri.startswith("urn:"), iri))
    canonical = ordered[0]
    return canonical, tuple(iri for iri in cluster.iris if iri != canonical)


#: Bare identifier value -> the IRI form the canonical layer uses as a node id.
#: v3 stores identifiers bare (§2.4 of the requirements) while node ids stay
#: URLs, so promotion has to re-expand exactly one of them.
_IRI_TEMPLATES: dict[str, str] = {
    "pulse:orcidIdentifier": "https://orcid.org/{}",
    "pulse:doi": "https://doi.org/{}",
    # `pulse:ror` is the deliberate exception: it is already URL-shaped.
    "pulse:ror": "{}",
}


def _iri_for(prop: str, value: Any) -> str | None:
    template = _IRI_TEMPLATES.get(prop)
    if template is None or not isinstance(value, str) or not value:
        return None
    return template.format(value)


def _select(
    cluster: Cluster,
    prop: str,
    resolver: TypeResolver,
    subject: str,
) -> tuple[Any, Selection | None]:
    """Choose one value for `prop`, and describe the choice."""
    candidates = cluster.values(prop)
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0], Selection(
            subject=subject,
            prop=prop,
            winner=candidates[0],
            losers=(),
            rule=RULE_ONLY_CANDIDATE,
            source_graph=_graph_asserting(cluster, prop, candidates[0]),
        )

    # Contested. Prefer the value from the most complete record — the source
    # that knew the most about this entity is the one most likely to be right
    # about any single field. Ties fall back to a stable order rather than to
    # iteration order, so two runs of the unifier over the same store agree.
    best_value: Any = None
    best_score = -1
    for record in cluster.records:
        values = record.values(prop)
        if not values:
            continue
        score = _identifier_count(record, resolver)
        if score > best_score:
            best_score, best_value = score, values[0]
    rule = RULE_MOST_COMPLETE_SOURCE
    if best_value is None or best_score == 0:
        best_value = min(candidates, key=str)
        rule = RULE_FIRST_STABLE

    losers = tuple(value for value in candidates if value != best_value)
    return best_value, Selection(
        subject=subject,
        prop=prop,
        winner=best_value,
        losers=losers,
        rule=rule,
        source_graph=_graph_asserting(cluster, prop, best_value),
    )


#: The rule name recorded for an aggregated value. Distinct from the SELECT
#: rules because nothing was *chosen*: the answer is a property of the whole
#: candidate set, not of the source that supplied the winner.
RULE_AGGREGATED = "aggregated"


def _aggregate(
    cluster: Cluster,
    prop: str,
    disposition: Disposition,
    subject: str,
) -> tuple[Any, Selection | None]:
    """The extremum of `prop` across the cluster, and a record of it.

    Comparable values only. A `pulse:contributionCount` that arrived as a
    string from one source and an integer from another cannot be ordered, and
    guessing a coercion here would silently pick by lexical order — so mixed
    types fall back to the SELECT path, where the choice is at least explained.
    """
    candidates = cluster.values(prop)
    if not candidates:
        return None, None
    kinds = {type(value) for value in candidates}
    if len(kinds) > 1 or not all(
        isinstance(value, (int, float, str)) for value in candidates
    ):
        logger.debug(
            "aggregate: %s has mixed value types %s; falling back to select",
            prop,
            sorted(kind.__name__ for kind in kinds),
        )
        return None, None
    winner = (
        max(candidates) if disposition is Disposition.MAX else min(candidates)
    )
    losers = tuple(value for value in candidates if value != winner)
    return winner, Selection(
        subject=subject,
        prop=prop,
        winner=winner,
        losers=losers,
        rule=f"{RULE_AGGREGATED}:{disposition.value}",
        source_graph=_graph_asserting(cluster, prop, winner),
    )


def _graph_asserting(cluster: Cluster, prop: str, value: Any) -> str | None:
    for record in cluster.records:
        if value in record.values(prop):
            return record.graph
    return None


def _is_reference(cluster: Cluster, prop: str) -> bool:
    """Whether this property's values were `{"@id": ...}` in the substrate.

    Read off the data rather than the shapes: `Record.values` flattens a
    reference to its IRI string, so the node has to be rebuilt in the form it
    came in or the JSON-LD context will serialise an edge as a literal — the
    same defect the reader's property-level `sh:or` bug caused (§3c).
    """
    for record in cluster.records:
        raw = record.properties.get(prop)
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if isinstance(item, dict) and item.get("@id"):
                return True
    return False


def merge_cluster(
    cluster: Cluster,
    policy: MergePolicy,
    *,
    capped: Iterable[str] | None = None,
) -> MergedEntity:
    """Collapse one cluster into a canonical node.

    `capped` is the set of properties the shapes give `sh:maxCount 1` **for
    this type**; anything in it is forced to SELECT, because a union would
    produce a graph the closed canonical shapes reject. It defaults to the
    generated cap set for the cluster's type, which is the right answer — pass
    it explicitly only to test the override.
    """
    resolver = policy.resolvers[cluster.entity_type]
    capped_set = (
        set(capped) if capped is not None else set(single_valued_for(cluster.entity_type))
    )
    # What the closed canonical shape can actually hold. The substrate's entity
    # shapes are open, so it may carry properties canonical has no slot for —
    # copying one through publishes a graph the enforcing gate then refuses.
    allowed = canonical_properties_for(cluster.entity_type)
    iri, aliases = _promoted_iri(cluster, resolver)

    properties: dict[str, Any] = {}
    selections: list[Selection] = []
    dropped: list[str] = []
    for prop in _properties_in(cluster.records):
        if allowed and prop not in allowed:
            # Not a loss: the substrate keeps it, and the substrate is the
            # durable layer. This is the layering doing its job — canonical is
            # the closed, queryable projection of it.
            dropped.append(prop)
            continue
        value, selection = _merge_property(
            cluster,
            prop,
            policy=policy,
            resolver=resolver,
            capped_set=capped_set,
            subject=iri,
        )
        if value is not None:
            properties[prop] = value
        if selection is not None:
            selections.append(selection)

    if dropped:
        logger.debug(
            "merge: %s dropped %s — not declared by the canonical shape",
            iri,
            sorted(dropped),
        )
    return MergedEntity(
        iri=iri,
        entity_type=cluster.entity_type,
        properties=properties,
        aliases=aliases,
        selections=tuple(selections),
        graphs=tuple(cluster.graphs),
        dropped=tuple(sorted(dropped)),
    )


def _merge_property(  # noqa: PLR0913, PLR0911 — four dispositions, four exits
    cluster: Cluster,
    prop: str,
    *,
    policy: MergePolicy,
    resolver: TypeResolver,
    capped_set: set[str],
    subject: str,
) -> tuple[Any, Selection | None]:
    """One property, merged per its disposition. Returns (value, record).

    Split out of `merge_cluster` so the four dispositions read as four
    branches rather than as one function over ruff's complexity threshold.
    A `None` value means "emit nothing for this property".
    """
    capped = prop in capped_set
    disposition = policy.disposition(prop, capped=capped)
    if disposition is Disposition.PER_RUN:
        return None, None

    reference = _is_reference(cluster, prop)
    if disposition is Disposition.UNION:
        values = cluster.values(prop)
        if not values:
            return None, None
        return [{"@id": value} if reference else value for value in values], None

    if disposition in {Disposition.MAX, Disposition.MIN}:
        value, selection = _aggregate(cluster, prop, disposition, subject)
        if value is None:
            return None, None
        return (value if capped else [value]), selection

    value, selection = _select(cluster, prop, resolver, subject)
    if value is None:
        return None, None
    # SELECT still emits a list where the shape allows one, because the
    # generated models type these as lists and a scalar would fail them.
    wrapped = {"@id": value} if reference else value
    return (wrapped if capped else [wrapped]), selection


def _properties_in(records: Sequence[Record]) -> list[str]:
    """Every property any record carries, in stable order."""
    seen: dict[str, None] = {}
    for record in records:
        for prop in record.properties:
            seen.setdefault(prop, None)
    return sorted(seen)


def contested_selections(entities: Iterable[MergedEntity]) -> list[Selection]:
    """Only the selections that actually decided something.

    What phase 5 will reify. A single-candidate selection is already attributed
    by its named graph and needs no record — the "derived only" grain.
    """
    return [
        selection
        for entity in entities
        for selection in entity.selections
        if selection.contested
    ]


__all__ = [
    "RULE_AGGREGATED",
    "RULE_FIRST_STABLE",
    "RULE_MOST_COMPLETE_SOURCE",
    "RULE_ONLY_CANDIDATE",
    "MergedEntity",
    "Selection",
    "contested_selections",
    "merge_cluster",
]
