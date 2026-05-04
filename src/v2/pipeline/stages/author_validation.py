"""Drop `schema:author` references that don't resolve to a `schema:Person`.

The Open Pulse `schema:author` shape requires the target to be a
`schema:Person`. Articles and Contributions in the graph occasionally
carry author refs that point to Memberships, Contributions, or
infoscience-keyed entities (no Person node exists at that id), which
trips a SHACL `Value does not have class schema1:Person` violation.

This stage runs after reconciliation/dedup and prunes such refs in
place. It does not drop the host entity; it only filters the
`schema:author` field.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.v2.pipeline.stages.models import AssembledOutput

PERSON_TYPE = "schema:Person"


def _types_of(entity: dict[str, Any]) -> set[str]:
    t = entity.get("type") or entity.get("@type")
    if isinstance(t, list):
        return {x for x in t if isinstance(x, str)}
    if isinstance(t, str):
        return {t}
    return set()


def _build_person_id_set(entities: list[Any]) -> set[str]:
    out: set[str] = set()
    for e in entities:
        if not isinstance(e, dict):
            continue
        if PERSON_TYPE in _types_of(e):
            eid = e.get("id") or e.get("@id")
            if isinstance(eid, str) and eid:
                out.add(eid)
    return out


def _filter_author_refs(value: Any, person_ids: set[str]) -> tuple[Any, int]:
    """Return (filtered_value, dropped_count)."""
    if value is None:
        return value, 0

    def _is_kept(ref: Any) -> bool:
        if isinstance(ref, str):
            return ref in person_ids
        if isinstance(ref, dict):
            target = ref.get("@id")
            if isinstance(target, str):
                return target in person_ids
        return True  # leave unrecognised shapes intact

    if isinstance(value, list):
        kept = [r for r in value if _is_kept(r)]
        return kept, len(value) - len(kept)
    if _is_kept(value):
        return value, 0
    return None, 1


def validate_author_classes(
    assembled: AssembledOutput,
) -> tuple[AssembledOutput, list[str]]:
    """Filter `schema:author` to refs that resolve to `schema:Person`."""
    candidates: list[Any] = []
    if isinstance(assembled.root_entity, dict):
        candidates.append(assembled.root_entity)
    candidates.extend(assembled.related_entities)

    person_ids = _build_person_id_set(candidates)

    new_root = (
        deepcopy(assembled.root_entity)
        if isinstance(assembled.root_entity, dict)
        else assembled.root_entity
    )
    new_related: list[Any] = [
        deepcopy(e) if isinstance(e, dict) else e
        for e in assembled.related_entities
    ]
    new_candidates: list[Any] = []
    if isinstance(new_root, dict):
        new_candidates.append(new_root)
    new_candidates.extend(new_related)

    warnings: list[str] = []

    for entity in new_candidates:
        if not isinstance(entity, dict):
            continue
        if "schema:author" not in entity:
            continue
        new_value, dropped = _filter_author_refs(
            entity["schema:author"], person_ids,
        )
        if dropped:
            entity_id = entity.get("id") or entity.get("@id") or "<unknown>"
            entity["schema:author"] = new_value
            warnings.append(
                f"Dropped {dropped} non-Person schema:author ref(s) on {entity_id}.",
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


__all__ = ["validate_author_classes"]
