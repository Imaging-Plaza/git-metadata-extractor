"""Drop or clean references that point to entities not present in the graph.

After ``strict_validation`` excludes entities that fail the v2.1.2 SHACL
constraints (e.g. an Organization missing all of `schema:identifier`,
`pulse:githubOrganizationHandle`, `pulse:infoscienceOrganizationIdentifier`),
the surviving entities can carry references to those excluded ids:

- A Repository's ``pulse:ownedBy`` may point to a github user URL that was
  never materialised as a Person (``Node X must conform to Person|Organization``).
- A Membership's ``org:organization`` may point to an Organization that was
  dropped (``Value does not have class org:Organization``).
- A Contribution's ``pulse:contributionTo`` may point to a Repository that
  was dropped.
- ``org:unitOf`` / ``org:hasUnit`` lists carry orphan entries.
- Persons' ``pulse:hasContribution`` / ``org:hasMembership`` lists outlive
  the targets they pointed to.

This stage walks the assembled graph and, in two passes:

1. Drops Memberships whose ``org:organization`` is dangling.
2. Drops Contributions whose ``pulse:contributionTo`` or ``schema:author``
   is dangling (we can't keep contribution data without a person+repo pair).
3. After re-indexing, filters all collection-typed reference fields
   (``org:unitOf``, ``org:hasUnit``, ``pulse:owns``, ``pulse:hasContribution``,
   ``org:hasMembership``) and clears ``pulse:ownedBy`` when the target is
   not in the live id set.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from git_metadata_extractor.pipeline.stages.models import AssembledOutput

MEMBERSHIP_TYPE = "org:Membership"
CONTRIBUTION_TYPE = "pulse:Contribution"

# Internal (`_`-prefixed → stripped from the default output) home for real
# owned-repository IRIs that aren't materialised as typed
# schema:SoftwareSourceCode nodes in this graph. Keeping them in public
# `pulse:owns` dangles the shape's `sh:class` constraint (1285 such
# violations observed extracting pallets/click — every contributor's full
# GitHub portfolio). The data is real (GitHub API), so move, don't drop.
INTERNAL_OWNS_KEY = "_owned_repositories"

LIST_REF_FIELDS: tuple[str, ...] = (
    "org:unitOf",
    "org:hasUnit",
    "pulse:owns",
    "pulse:hasContribution",
    "org:hasMembership",
)
# Scalar reference fields that the prune stage clears when the target isn't
# in the assembled graph.
#
# `pulse:isForkOf` is deliberately NOT here: it points to the upstream fork
# parent (e.g. lovell/detect-libc) which is an external repo we don't ingest.
# Clearing it loses the meaningful provenance signal that this repo is a fork
# of X, with no benefit — SHACL `sh:class schema:SoftwareSourceCode` on an
# unknown IRI is treated as open-world / non-violating by the validator
# (verified: conforms=True with the value preserved as a URI literal).
SCALAR_REF_FIELDS: tuple[str, ...] = (
    "pulse:ownedBy",
)


def _types_of(entity: dict[str, Any]) -> set[str]:
    raw = entity.get("type") or entity.get("@type")
    if isinstance(raw, list):
        return {x for x in raw if isinstance(x, str)}
    if isinstance(raw, str):
        return {raw}
    return set()


def _resolve_id_ref(value: Any) -> str | None:
    if isinstance(value, dict):
        target = value.get("@id") or value.get("id")
        return target if isinstance(target, str) else None
    if isinstance(value, str):
        return value
    return None


def _live_ids(candidates: list[Any]) -> set[str]:
    out: set[str] = set()
    for entity in candidates:
        if not isinstance(entity, dict):
            continue
        eid = entity.get("id") or entity.get("@id")
        if isinstance(eid, str) and eid:
            out.add(eid)
    return out


def _filter_list_refs(value: Any, live: set[str]) -> tuple[Any, int]:
    """Return ``(filtered_list, dropped_count)`` for a list of @id refs."""

    if not isinstance(value, list):
        return value, 0
    kept: list[Any] = []
    dropped = 0
    for ref in value:
        target = _resolve_id_ref(ref)
        if target is None or target in live:
            kept.append(ref)
        else:
            dropped += 1
    return kept, dropped


def _split_owns_refs(value: Any, live: set[str]) -> tuple[Any, list[str], int]:
    """Split a `pulse:owns` list into (kept_public, external_internal, dropped).

    `pulse:owns` requires its target to be a typed
    ``schema:SoftwareSourceCode`` node (shape ``sh:class``). Entries that
    resolve to a live in-graph repo stay public. Real but un-materialised
    owned repos — well-formed ``https://…`` IRIs not in ``live`` (e.g. a
    contributor's wider GitHub portfolio when ``V2_EXPAND_OWNED_REPOS`` is
    off) — are genuine data but would dangle the constraint, so they are
    returned separately to move onto the internal ``_owned_repositories``
    field. Mangled / non-IRI refs are dropped.
    """

    if not isinstance(value, list):
        return value, [], 0
    kept: list[Any] = []
    external: list[str] = []
    dropped = 0
    for ref in value:
        target = _resolve_id_ref(ref)
        if target is None or target in live:
            kept.append(ref)
            continue
        if isinstance(target, str) and target.startswith(("http://", "https://")):
            external.append(target)
        else:
            dropped += 1
    return kept, external, dropped


def _clear_scalar_if_dangling(value: Any, live: set[str]) -> tuple[Any, bool]:
    """Return ``(maybe-cleared, was_cleared)`` for a single @id ref."""

    target = _resolve_id_ref(value)
    if target is None:
        return value, False
    if target in live:
        return value, False
    return None, True


def prune_dangling_refs(
    assembled: AssembledOutput,
) -> tuple[AssembledOutput, list[str]]:
    """Remove references pointing to entities not in the assembled graph.

    Mutates a deep copy. Returns the cleaned ``AssembledOutput`` plus
    human-readable warnings for each pruning action.
    """

    new_root = (
        deepcopy(assembled.root_entity)
        if isinstance(assembled.root_entity, dict)
        else assembled.root_entity
    )
    new_related: list[Any] = [
        deepcopy(e) if isinstance(e, dict) else e for e in assembled.related_entities
    ]

    def _candidates() -> list[Any]:
        out: list[Any] = []
        if isinstance(new_root, dict):
            out.append(new_root)
        out.extend(new_related)
        return out

    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Pass 1: drop Memberships and Contributions whose core refs are dangling
    # ------------------------------------------------------------------
    live = _live_ids(_candidates())
    surviving: list[Any] = []
    dropped_memberships = 0
    dropped_contribs = 0
    for entity in new_related:
        if not isinstance(entity, dict):
            surviving.append(entity)
            continue
        types = _types_of(entity)
        if MEMBERSHIP_TYPE in types:
            # pulse:MembershipShape requires org:organization. A missing ref
            # is just as fatal as a dangling one — both produce a SHACL
            # violation on upload, so treat them identically.
            org_ref = _resolve_id_ref(entity.get("org:organization"))
            membership_problems: list[str] = []
            if org_ref is None:
                membership_problems.append("org:organization (missing)")
            elif org_ref not in live:
                membership_problems.append(
                    f"org:organization={org_ref!r} (dangling)",
                )
            if membership_problems:
                dropped_memberships += 1
                warnings.append(
                    f"prune_dangling_refs: dropped Membership "
                    f"{entity.get('id') or entity.get('@id')!r} — "
                    + ", ".join(membership_problems),
                )
            else:
                surviving.append(entity)
            continue
        if CONTRIBUTION_TYPE in types:
            # pulse:ContributionShape mandates exactly one schema:author and
            # one pulse:contributionTo per node. Treat missing fields and
            # dangling refs identically — both produce SHACL violations on
            # upload (observed: 19 orphan Contributions in the
            # `infoscience-hybrid` named graph leaked past the old guard
            # because it only matched dangling, not missing, refs).
            repo_ref = _resolve_id_ref(entity.get("pulse:contributionTo"))
            author_ref = _resolve_id_ref(entity.get("schema:author"))
            contribution_problems: list[str] = []
            if repo_ref is None:
                contribution_problems.append("pulse:contributionTo (missing)")
            elif repo_ref not in live:
                contribution_problems.append(
                    f"pulse:contributionTo={repo_ref!r} (dangling)",
                )
            if author_ref is None:
                contribution_problems.append("schema:author (missing)")
            elif author_ref not in live:
                contribution_problems.append(
                    f"schema:author={author_ref!r} (dangling)",
                )
            if contribution_problems:
                dropped_contribs += 1
                warnings.append(
                    f"prune_dangling_refs: dropped Contribution "
                    f"{entity.get('id') or entity.get('@id')!r} — "
                    + ", ".join(contribution_problems),
                )
            else:
                surviving.append(entity)
            continue
        surviving.append(entity)
    new_related = surviving

    # ------------------------------------------------------------------
    # Pass 2: filter list-typed ref fields and clear scalar refs
    # ------------------------------------------------------------------
    live = _live_ids(_candidates())  # recompute after pass-1 drops
    cleared_scalars = 0
    filtered_list_entries = 0
    moved_owns = 0
    for entity in _candidates():
        if not isinstance(entity, dict):
            continue
        for field in LIST_REF_FIELDS:
            if field not in entity:
                continue
            if field == "pulse:owns":
                # Keep live in-graph repos public; move real-but-
                # unmaterialised external repo IRIs to the internal
                # `_owned_repositories` field (preserved, stripped from
                # the default output) so they don't dangle the shape's
                # `sh:class schema:SoftwareSourceCode` constraint. Mangled
                # refs are dropped.
                kept, external, dropped = _split_owns_refs(entity[field], live)
                entity[field] = kept or None
                if external:
                    existing = entity.get(INTERNAL_OWNS_KEY)
                    merged = list(existing) if isinstance(existing, list) else []
                    seen = set(merged)
                    for iri in external:
                        if iri not in seen:
                            merged.append(iri)
                            seen.add(iri)
                    entity[INTERNAL_OWNS_KEY] = merged
                    moved_owns += len(external)
                filtered_list_entries += dropped
                continue
            new_value, dropped = _filter_list_refs(entity[field], live)
            if dropped:
                entity[field] = new_value
                filtered_list_entries += dropped
        for field in SCALAR_REF_FIELDS:
            if field not in entity:
                continue
            new_value, was_cleared = _clear_scalar_if_dangling(entity[field], live)
            if was_cleared:
                entity[field] = new_value
                cleared_scalars += 1

    # ------------------------------------------------------------------
    # Pass 3: drop orphan Organizations
    # ------------------------------------------------------------------
    # An Organization with no incoming reference contributes nothing to
    # the graph and signals a false-positive from a name lookup that
    # didn't survive subsequent filters (Statistics Botswana case:
    # Membership dropped because role/dates were all null, leaving the
    # Org orphan in the graph). Drop the Org so the user doesn't see
    # phantom affiliations.
    org_ref_keys = (
        "org:organization",        # on Memberships
        "pulse:ownedBy",           # on Repositories / Articles
        "org:hasUnit",             # on parent Orgs
        "org:unitOf",              # on child Orgs
        "pulse:owns",              # rarely points at orgs but covered
        "schema:sourceOrganization",  # on Articles
        "organizationId",          # inside affiliation dicts
    )
    referenced_ids: set[str] = set()
    for entity in _candidates():
        if not isinstance(entity, dict):
            continue
        for key, value in entity.items():
            if key not in org_ref_keys:
                continue
            if isinstance(value, str) and value:
                referenced_ids.add(value)
            elif isinstance(value, dict):
                target = value.get("@id") or value.get("id")
                if isinstance(target, str) and target:
                    referenced_ids.add(target)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item:
                        referenced_ids.add(item)
                    elif isinstance(item, dict):
                        target = item.get("@id") or item.get("id")
                        if isinstance(target, str) and target:
                            referenced_ids.add(target)
        # `affiliations` lives one level deeper on Persons.
        affiliations = entity.get("affiliations")
        if isinstance(affiliations, list):
            for aff in affiliations:
                if isinstance(aff, dict):
                    target = aff.get("organizationId") or aff.get("organization")
                    if isinstance(target, str) and target:
                        referenced_ids.add(target)
                elif isinstance(aff, str) and aff:
                    referenced_ids.add(aff)

    surviving_after_orphans: list[Any] = []
    dropped_orphan_orgs = 0
    for entity in new_related:
        if not isinstance(entity, dict):
            surviving_after_orphans.append(entity)
            continue
        types = _types_of(entity)
        if "org:Organization" not in types:
            surviving_after_orphans.append(entity)
            continue
        org_id = entity.get("id") or entity.get("@id")
        if isinstance(org_id, str) and org_id in referenced_ids:
            surviving_after_orphans.append(entity)
            continue
        dropped_orphan_orgs += 1
        warnings.append(
            f"prune_dangling_refs: dropped orphan Organization "
            f"{org_id!r} (no incoming reference after Membership filter).",
        )
    new_related = surviving_after_orphans

    if (
        dropped_memberships or dropped_contribs or cleared_scalars
        or filtered_list_entries or dropped_orphan_orgs or moved_owns
    ):
        warnings.append(
            "prune_dangling_refs: summary "
            f"dropped_memberships={dropped_memberships} "
            f"dropped_contributions={dropped_contribs} "
            f"dropped_orphan_orgs={dropped_orphan_orgs} "
            f"cleared_scalar_refs={cleared_scalars} "
            f"filtered_list_entries={filtered_list_entries} "
            f"moved_owns_to_internal={moved_owns}",
        )

    return (
        AssembledOutput(
            root_entity=new_root,
            related_entities=new_related,
            excluded_entities=list(assembled.excluded_entities),
            warnings=list(assembled.warnings),
        ),
        warnings,
    )


__all__ = ["prune_dangling_refs"]
