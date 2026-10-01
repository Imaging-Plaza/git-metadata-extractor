"""Group substrate records that describe the same entity. Type-agnostic.

The half of unification that does not vary by type: union-find over match keys,
where the keys themselves come from `unify.policy`. Every per-type judgement —
which keys, in what order, what the winner is called — lives there.

`pipeline/stages/reconciliation.py` already does this **within one request**
(`_organization_equivalence_groups`, with its own `_find` / `_union`). This is
the cross-run version, and the difference is not the algorithm but the input:
reconciliation sees one repository's entities, this sees every run the store has
accumulated. That is the whole reason unification moved store-side — no single
`/v2/extract` can see one person across two repositories.

**Match-key order is trusted, not scored.** A match on an earlier key wins
outright; later keys only cluster records nothing more precise has separated.
So two people sharing a name are not merged when their ORCIDs differ, because
`pulse:orcidIdentifier` is consulted first and a *disagreement* there blocks the
weaker match. Without that block, name-matching alone silently fuses distinct
researchers — the failure mode that makes dedup worse than no dedup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from git_metadata_extractor.unify.policy import TypeResolver


@dataclass(slots=True)
class Record:
    """One entity as one run asserted it."""

    iri: str
    entity_type: str
    #: The graph it came from. Kept because it is the provenance handle: the
    #: named graph is what attributes these triples to a source.
    graph: str
    properties: dict[str, Any] = field(default_factory=dict)

    def values(self, prop: str) -> list[Any]:
        """`prop`'s values as a flat list, references reduced to their IRI."""
        raw = self.properties.get(prop)
        if raw is None:
            return []
        items = raw if isinstance(raw, list) else [raw]
        out: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                ref = item.get("@id")
                if ref:
                    out.append(str(ref))
            elif item not in (None, ""):
                out.append(item)
        return out


@dataclass(slots=True)
class Cluster:
    """Records agreed to describe one entity."""

    entity_type: str
    records: list[Record] = field(default_factory=list)

    @property
    def iris(self) -> list[str]:
        """Distinct source IRIs, in first-seen order."""
        seen: dict[str, None] = {}
        for record in self.records:
            seen.setdefault(record.iri, None)
        return list(seen)

    @property
    def graphs(self) -> list[str]:
        seen: dict[str, None] = {}
        for record in self.records:
            seen.setdefault(record.graph, None)
        return list(seen)

    def values(self, prop: str) -> list[Any]:
        """Every value any record gives for `prop`, deduplicated, in order."""
        seen: dict[str, Any] = {}
        for record in self.records:
            for value in record.values(prop):
                seen.setdefault(_value_key(value), value)
        return list(seen.values())


def _value_key(value: Any) -> str:
    return f"{type(value).__name__}:{value}"


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, index: int) -> int:
        while self._parent[index] != index:
            self._parent[index] = self._parent[self._parent[index]]
            index = self._parent[index]
        return index

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self._parent[b] = a


def _key_index(
    records: Sequence[Record],
    prop: str,
) -> tuple[dict[Any, list[int]], dict[int, set[Any]]]:
    """`value -> record indices` and `record index -> its values` for one key."""
    by_value: dict[Any, list[int]] = {}
    by_record: dict[int, set[Any]] = {}
    for index, record in enumerate(records):
        values = record.values(prop)
        if not values:
            continue
        by_record[index] = set(values)
        for value in values:
            by_value.setdefault(value, []).append(index)
    return by_value, by_record


def cluster_records(
    records: Iterable[Record],
    resolver: TypeResolver,
) -> list[Cluster]:
    """Cluster `records` of one type using `resolver`'s ordered match keys.

    Records sharing the *same IRI* always cluster: two runs asserting things
    about `https://orcid.org/0000-...` are describing one entity by
    construction, whatever their other properties say. That is the common case
    in practice — extraction resolves identity per request — and the match keys
    are what add the cases it cannot see.
    """
    items = list(records)
    if not items:
        return []

    finder = _UnionFind(len(items))

    # Same IRI, same entity. Unconditional and first.
    by_iri: dict[str, list[int]] = {}
    for index, record in enumerate(items):
        by_iri.setdefault(record.iri, []).append(index)
    for indices in by_iri.values():
        for other in indices[1:]:
            finder.union(indices[0], other)

    # Then each match key in precision order, with a veto from the stronger
    # keys already consulted.
    consulted: list[tuple[dict[Any, list[int]], dict[int, set[Any]]]] = []
    for prop in resolver.match_keys:
        by_value, by_record = _key_index(items, prop)
        for indices in by_value.values():
            first = indices[0]
            for other in indices[1:]:
                if _contradicted(first, other, consulted):
                    continue
                finder.union(first, other)
        consulted.append((by_value, by_record))

    grouped: dict[int, Cluster] = {}
    for index, record in enumerate(items):
        root = finder.find(index)
        grouped.setdefault(
            root,
            Cluster(entity_type=resolver.entity_type),
        ).records.append(record)
    return list(grouped.values())


def _contradicted(
    left: int,
    right: int,
    consulted: Sequence[tuple[dict[Any, list[int]], dict[int, set[Any]]]],
) -> bool:
    """Whether a more precise key already says these are different entities.

    Both records must *have* the stronger key for it to speak. One record
    carrying an ORCID and another carrying none says nothing — absence is not
    disagreement, and treating it as such would refuse every merge that adds
    information, which is the only kind worth making.
    """
    for _by_value, by_record in consulted:
        a, b = by_record.get(left), by_record.get(right)
        if a and b and not (a & b):
            return True
    return False


def group_by_type(records: Iterable[Record]) -> dict[str, list[Record]]:
    """Partition records by `@type`, since resolvers are per type."""
    out: dict[str, list[Record]] = {}
    for record in records:
        out.setdefault(record.entity_type, []).append(record)
    return out


__all__ = [
    "Cluster",
    "Record",
    "cluster_records",
    "group_by_type",
]
