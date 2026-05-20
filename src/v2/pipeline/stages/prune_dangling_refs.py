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

from src.v2.pipeline.stages.models import AssembledOutput

MEMBERSHIP_TYPE = "org:Membership"
CONTRIBUTION_TYPE = "pulse:Contribution"

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
    for entity in _candidates():
        if not isinstance(entity, dict):
            continue
        for field in LIST_REF_FIELDS:
            if field not in entity:
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

    if dropped_memberships or dropped_contribs or cleared_scalars or filtered_list_entries:
        warnings.append(
            "prune_dangling_refs: summary "
            f"dropped_memberships={dropped_memberships} "
            f"dropped_contributions={dropped_contribs} "
            f"cleared_scalar_refs={cleared_scalars} "
            f"filtered_list_entries={filtered_list_entries}",
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
