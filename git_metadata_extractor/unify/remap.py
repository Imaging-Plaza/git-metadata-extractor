"""Rewrite references after unification renames an entity.

When a cluster's canonical IRI is not one of its source IRIs — a person known
by a GitHub handle in one run and resolved to an ORCID in another — every edge
that pointed at the old IRI now dangles. `graph:canonical` is validated by
closed shapes with `sh:class` constraints, so a dangling `schema:author` is a
violation; worse, `prune_dangling_refs` is a *pipeline* stage and does not run
here, so nothing downstream would catch it.

Three rewrites, and the last two are the ones that are easy to miss:

1. **References.** Any `{"@id": old}` becomes `{"@id": new}`.
2. **Composite ids.** `org:Membership` and `pulse:Contribution` are identified
   by `{a}__{b}` over their endpoints, so renaming an endpoint changes the
   *identity* of the edge, not just a field inside it. Rewriting the reference
   and leaving the id is how you get a membership whose id claims one person
   and whose `schema:author` claims another.
3. **Selections.** `merge` records what it chose *before* this pass runs, so a
   `Selection` whose winner is a reference can name an IRI that the remap then
   replaces. The provenance writer quotes that value into
   `<< s p o >>`, and a quoted triple need not exist to be annotated — so the
   annotation would attach to nothing, silently. It was found because the
   prune step then deleted it on every pass: a live store showed "1 new, 1
   reconfirmed" forever instead of a counter that climbs.

`pipeline/stages/reconciliation.py::_apply_remaps` does exactly this within one
request. This is the same operation over the store's accumulated graph, and the
double-underscore convention it parses is the one in `AGENTS.md`: emit `__`,
accept a single `_` when reading, for graphs written before the convention
changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from git_metadata_extractor.unify.merge import MergedEntity

#: Types whose IRI is a composite of their endpoints, so a renamed endpoint
#: changes their identity. Both are derived edges.
COMPOSITE_TYPES: frozenset[str] = frozenset({"org:Membership", "pulse:Contribution"})

COMPOSITE_SEPARATOR = "__"


def alias_map(entities: Iterable[MergedEntity]) -> dict[str, str]:
    """`old IRI -> canonical IRI` for every entity the unifier renamed.

    Self-mappings are excluded so callers can treat a hit as "this changed".
    """
    return {
        alias: entity.iri
        for entity in entities
        for alias in entity.aliases
        if alias != entity.iri
    }


def _remap_value(value: Any, mapping: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        ref = value.get("@id")
        if isinstance(ref, str) and ref in mapping:
            return {**value, "@id": mapping[ref]}
        return {key: _remap_value(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_value(item, mapping) for item in value]
    return value


def _remap_composite_id(iri: str, mapping: Mapping[str, str]) -> str:
    """Rebuild a `{a}__{b}` id from its remapped endpoints.

    Split on `__` only. A single `_` is accepted when *parsing* legacy graphs
    per the id conventions, but splitting on it here would shred any IRI
    containing an underscore — `github.com/some_org/repo` among them — so the
    legacy form is deliberately left alone rather than half-handled.
    """
    if COMPOSITE_SEPARATOR not in iri:
        return iri
    left, _, right = iri.partition(COMPOSITE_SEPARATOR)
    new_left, new_right = mapping.get(left, left), mapping.get(right, right)
    if (new_left, new_right) == (left, right):
        return iri
    return f"{new_left}{COMPOSITE_SEPARATOR}{new_right}"


def remap_entities(
    entities: Iterable[MergedEntity],
    mapping: Mapping[str, str],
) -> list[MergedEntity]:
    """Apply `mapping` to every reference and composite id, in place.

    Returns the same objects rather than copies: they were built by this
    pipeline moments earlier and nothing else holds them, so copying would only
    obscure that the remap is a fix-up pass over one batch.
    """
    items = list(entities)
    if not mapping:
        return items
    for entity in items:
        entity.properties = {
            prop: _remap_value(value, mapping)
            for prop, value in entity.properties.items()
        }
        if entity.entity_type in COMPOSITE_TYPES:
            entity.iri = _remap_composite_id(entity.iri, mapping)
        entity.selections = tuple(
            _remap_selection(selection, mapping, subject=entity.iri)
            for selection in entity.selections
        )
    return items


def _remap_selection(selection: Any, mapping: Mapping[str, str], *, subject: str) -> Any:
    """A `Selection` rewritten to describe the triple canonical actually holds.

    The subject is taken from the entity rather than remapped, because a
    composite id is rebuilt from its endpoints and is not itself in `mapping`.
    """
    from dataclasses import replace  # noqa: PLC0415

    winner = mapping.get(str(selection.winner), selection.winner)
    # Losers that remap onto the winner were never a disagreement: two runs
    # named the same entity differently, and resolving identity dissolved the
    # conflict. Keeping them would report a contested value whose winner and
    # loser print identically, and would write a provenance annotation for a
    # decision nobody made.
    losers = tuple(
        remapped
        for loser in selection.losers
        if (remapped := mapping.get(str(loser), loser)) != winner
    )
    if (subject, winner, losers) == (selection.subject, selection.winner, selection.losers):
        return selection
    return replace(selection, subject=subject, winner=winner, losers=losers)


def stale_references(
    entities: Iterable[MergedEntity],
    mapping: Mapping[str, str],
) -> dict[str, list[str]]:
    """References still pointing at an IRI the unifier renamed away.

    The sharp check, and a real invariant: after `remap_entities` this must be
    empty. Unlike `dangling_references` it cannot report a false positive —
    every IRI in `mapping` was an entity in this batch and is now something
    else, so a reference to it is broken by construction.

    Worth having both. `dangling_references` answers "does anything point at a
    node that is not here", which has legitimate answers — an SPDX licence, a
    Wikidata discipline, a `pulse:PlatformEnumeration` member. This answers
    "did the remap miss something", which has exactly one acceptable answer.
    """
    known = set(mapping)
    if not known:
        return {}
    out: dict[str, list[str]] = {}
    for entity in entities:
        stale = sorted(
            {
                ref
                for value in entity.properties.values()
                for ref in _references(value)
                if ref in known
            },
        )
        if stale:
            out[entity.iri] = stale
    return out


#: The ontology's own term namespace. Every `pulse:GitHub`, `pulse:Software`
#: and `pulse:PublicVisibility` lives here and **no entity ever does** —
#: entities are `https://github.com/...`, `https://orcid.org/...`,
#: `https://ror.org/...` or `urn:pulse:...`. So excluding it from the dangling
#: report is precise rather than a heuristic.
#:
#: It matters because the two read paths present these values differently: the
#: JSON substrate carries `"pulse:platform": "pulse:GitHub"` and relies on the
#: context's `@type: @id`, while SPARQL returns the same triple as a URI. Left
#: unfiltered the report read 55 unresolved references offline and 293 from the
#: store — for identical data.
VOCABULARY_NAMESPACE = "https://open-pulse.epfl.ch/ontology#"


def dangling_references(entities: Iterable[MergedEntity]) -> dict[str, list[str]]:
    """References whose target is not an entity in this batch.

    Not every dangling reference is a bug, and most are not: the canonical
    graph legitimately points at nodes the unifier does not own — an SPDX
    licence, a Wikidata discipline. So this reports rather than prunes, and it
    is a diagnostic rather than an invariant. Use `stale_references` for the
    invariant.

    Vocabulary terms are excluded (see `VOCABULARY_NAMESPACE`); external *data*
    references are not, because those are worth seeing.

    What the *shape* of this report tells you is whether a class of node is
    being skipped. Before the profile resolvers existed, every person in the
    corpus reported a dangling `pulse:hasProfile`; with them, the only
    unresolved references left over the whole 119-run corpus are 55 SPDX
    licence URLs.
    """
    known = {entity.iri for entity in entities}
    out: dict[str, list[str]] = {}
    for entity in entities:
        missing = sorted(
            {
                ref
                for value in entity.properties.values()
                for ref in _references(value)
                if ref not in known and not ref.startswith(VOCABULARY_NAMESPACE)
            },
        )
        if missing:
            out[entity.iri] = missing
    return out


def _references(value: Any) -> list[str]:
    if isinstance(value, dict):
        ref = value.get("@id")
        return [str(ref)] if isinstance(ref, str) else []
    if isinstance(value, list):
        return [ref for item in value for ref in _references(item)]
    return []


__all__ = [
    "COMPOSITE_SEPARATOR",
    "COMPOSITE_TYPES",
    "VOCABULARY_NAMESPACE",
    "alias_map",
    "dangling_references",
    "remap_entities",
    "stale_references",
]
