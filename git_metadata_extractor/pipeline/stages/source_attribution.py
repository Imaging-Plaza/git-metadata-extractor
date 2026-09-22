"""Which source asserted which property — the table `raw_projection` routes by.

`_platform_of` answers "which platform does this entity belong to" with one
answer per entity, and its own docstring records why that is not enough:

    Which source asserted each individual value is a finer question than one
    anchor per entity can answer.

This module answers the finer question for the properties where the flat
intermediate actually carries the evidence. A person discovered on GitHub and
resolved to an ORCID iD is asserted by *two* sources, and the substrate is
already shaped to say so: one named graph per `pulse:ExtractionOutput`, the
same entity IRI appearing in as many slices as there are sources. The unifier
clusters them back together unconditionally — `cluster.py` treats the same IRI
as the same entity, first and without a vote — so splitting costs nothing
downstream and buys the attribution.

**Why a hand-written table rather than something read off the shapes.** The
same reason `unify/policy.py` is hand-written: the shapes describe what a
property *is*, not who said it. `pulse:ror` and `schema:name` are both plain
properties of an organization; that the first can only have come from ROR and
the second from whatever platform described the org is knowledge about the
pipeline, not about the ontology. What a test *can* check against the TTL is
that every property named here is one some raw shape declares, and
`tests/v2/test_source_attribution.py` does.

**Conservative by construction.** A property with no entry stays on the
entity's anchor slice, which is exactly where the whole entity sat before this
module existed. So an entity carrying no cross-source evidence projects
byte-identically to the way it did, and the corpus diff is confined to the
entities that really do have two sources.

**What this does not claim.** The attribution is derived from identity
evidence on the projected node, not from an observation of which provider call
produced which value. When an LLM agent reads an ORCID RAG hit and writes the
name it found there into `schema:name`, nothing in the payload records that,
and this module will leave that name on the anchor slice. Recording the *asking*
is a different mechanism with a different honesty guarantee — see
`extraction_run.build_query_activities`, which writes what each run queried
without claiming which triple it produced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Properties only one source can have asserted, by construction.
#:
#: Both are identifiers minted by a registry: a value here did not come from
#: the platform that described the rest of the entity, it came from the
#: registry that issued it. That is the entire content of the claim, and it is
#: why the table is short — `schema:name` is *not* here, because a name on a
#: GitHub-anchored person may equally have come from GitHub or from ORCID and
#: the projected node does not say which.
SOURCE_BY_PROPERTY: dict[str, str] = {
    "pulse:orcidIdentifier": "pulse:ORCID",
    "pulse:ror": "pulse:ROR",
}

#: Properties whose *values* are references to profile nodes, each of which
#: names its own platform. Routed per value rather than per property: a person
#: with both a GitHub and an Infoscience profile has one reference belonging in
#: each slice, which no property-level entry could express.
PROFILE_REF_PROPERTIES: tuple[str, ...] = (
    "pulse:hasProfile",
    "pulse:hasOrganizationProfile",
)

#: Never routed away from the anchor: they identify the node rather than say
#: anything about it, so every slice needs its own copy.
_IDENTITY_KEYS: frozenset[str] = frozenset({"@id", "@type"})


def _refs(value: Any) -> list[dict[str, Any]]:
    """`value` as a list of reference dicts, tolerating the single-value form."""
    items = value if isinstance(value, list) else [value]
    return [item for item in items if isinstance(item, dict) and item.get("@id")]


def split_by_source(
    entity: Mapping[str, Any],
    profiles: list[dict[str, Any]],
    *,
    anchor: str | None,
) -> list[tuple[str | None, dict[str, Any]]]:
    """Split one projected raw node into one node per asserting source.

    Returns `(platform, node)` pairs, the anchor slice first. Every slice
    carries `@id` and `@type`; the properties are partitioned between them.
    A slice that would hold nothing but its identity is dropped, so an entity
    with no cross-source evidence comes back as the single anchor slice it has
    always been.

    `anchor` is `_platform_of`'s answer and may be `None` — an entity no
    platform holds. That case returns the node unsplit and unanchored, because
    a slice that cannot name its output cannot be written to a named graph
    either.
    """
    if anchor is None:
        return [(None, dict(entity))]

    platform_of_profile = {
        str(profile["@id"]): str(profile.get("pulse:platform") or "")
        for profile in profiles
        if profile.get("@id")
    }

    slices: dict[str, dict[str, Any]] = {}

    def slice_for(platform: str) -> dict[str, Any]:
        existing = slices.get(platform)
        if existing is None:
            existing = {key: entity[key] for key in _IDENTITY_KEYS if key in entity}
            slices[platform] = existing
        return existing

    # The anchor slice always exists, and always comes first: it is the one
    # that keeps everything unattributed, so an empty one means the entity's
    # every property was attributed elsewhere.
    slice_for(anchor)

    for key, value in entity.items():
        if key in _IDENTITY_KEYS:
            continue
        if key in PROFILE_REF_PROPERTIES:
            for ref in _refs(value):
                platform = platform_of_profile.get(str(ref["@id"])) or anchor
                slice_for(platform).setdefault(key, []).append(ref)
            continue
        slice_for(SOURCE_BY_PROPERTY.get(key, anchor))[key] = value

    return [
        (platform, node)
        for platform, node in sorted(
            slices.items(),
            key=lambda item: (item[0] != anchor, item[0]),
        )
        if set(node) - _IDENTITY_KEYS
    ]


__all__ = [
    "PROFILE_REF_PROPERTIES",
    "SOURCE_BY_PROPERTY",
    "split_by_source",
]
