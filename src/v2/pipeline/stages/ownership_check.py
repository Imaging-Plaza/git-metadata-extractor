"""Deterministic ownership validation for assembled output.

Two independent checks, both pure-function, no LLM, no network:

1. **Type check** — only `schema:Person` and `org:Organization` entities may
   carry `pulse:owns`. If any other entity type has `pulse:owns`, the field is
   stripped (and a warning is emitted).
2. **GitHub-handle check** — for every `pulse:owns` entry that points at a
   `https://github.com/<owner>/<repo>` URL, `<owner>` (case-insensitive) must
   match the source entity's GitHub handle:
       - Person: `pulse:githubUsername`
       - Organization: `pulse:githubOrganizationHandle`
   Mismatched entries are dropped from `pulse:owns`. Entries that aren't
   github.com URLs are left alone (other forges aren't validated yet).
   When the source entity has no GitHub handle, the entry passes through.

Run as a stage between `apply_link_pruning_to_assembled_output` and
`build_jsonld_output`.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from src.v2.pipeline.stages.models import AssembledOutput

OWNS_KEY = "pulse:owns"
OWNERS_BY_TYPE: dict[str, str] = {
    "schema:Person": "pulse:githubUsername",
    "org:Organization": "pulse:githubOrganizationHandle",
}


def _extract_github_owner_from_url(value: Any) -> str | None:
    """Return `<owner>` for `https://github.com/<owner>/<repo>`, else `None`.

    Non-github URLs and malformed paths return `None` so the caller can skip
    validation rather than raising.
    """
    if isinstance(value, dict):
        value = value.get("@id") or value.get("id")
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        return None
    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 2:
        return None
    return parts[0]


def _entity_owner_handle(entity: dict[str, Any]) -> str | None:
    handle_field = OWNERS_BY_TYPE.get(entity.get("type") or "")
    if handle_field is None:
        return None
    handle = entity.get(handle_field)
    if isinstance(handle, str) and handle.strip():
        return handle.strip().lower()
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        nested = identifiers.get(handle_field)
        if isinstance(nested, str) and nested.strip():
            return nested.strip().lower()
    return None


def _entity_label(entity: dict[str, Any]) -> str:
    return (
        (entity.get("id") if isinstance(entity.get("id"), str) else None)
        or (entity.get("schema:name") if isinstance(entity.get("schema:name"), str) else None)
        or "<unknown entity>"
    )


def _filter_owns_for_entity(
    entity: dict[str, Any],
) -> tuple[list[Any], list[str]]:
    """Return (kept_owns, warnings) after applying the github-handle check."""
    raw_owns = entity.get(OWNS_KEY)
    if not isinstance(raw_owns, list):
        return [], []
    handle = _entity_owner_handle(entity)
    label = _entity_label(entity)

    kept: list[Any] = []
    warnings: list[str] = []
    for entry in raw_owns:
        owner_in_url = _extract_github_owner_from_url(entry)
        if owner_in_url is None:
            # Not a github URL we can validate — preserve verbatim.
            kept.append(entry)
            continue
        if handle is None:
            # No handle on the source entity — can't validate.
            kept.append(entry)
            continue
        if owner_in_url.lower() == handle:
            kept.append(entry)
            continue
        target = entry.get("@id") or entry.get("id") if isinstance(entry, dict) else entry
        warnings.append(
            f"Dropped pulse:owns entry on {label}: repository {target} owner "
            f"'{owner_in_url}' does not match entity handle '{handle}'.",
        )
    return kept, warnings


def _apply_owns_check(
    entity: dict[str, Any],
    warnings: list[str],
) -> None:
    """In-place mutation of `entity` enforcing both ownership rules."""
    if OWNS_KEY not in entity:
        return
    entity_type = entity.get("type")
    if entity_type not in OWNERS_BY_TYPE:
        warnings.append(
            f"Stripped pulse:owns from {_entity_label(entity)}: entity type "
            f"'{entity_type}' is not Person or Organization.",
        )
        entity[OWNS_KEY] = None
        return
    kept, entity_warnings = _filter_owns_for_entity(entity)
    warnings.extend(entity_warnings)
    entity[OWNS_KEY] = kept if kept else None


def validate_ownership(
    assembled: AssembledOutput,
) -> tuple[AssembledOutput, list[str]]:
    """Return a copy of `assembled` with invalid `pulse:owns` entries dropped."""
    warnings: list[str] = []

    new_root: dict[str, Any] | None = None
    if isinstance(assembled.root_entity, dict):
        new_root = deepcopy(assembled.root_entity)
        _apply_owns_check(new_root, warnings)

    new_related: list[dict[str, Any]] = []
    for entity in assembled.related_entities:
        if not isinstance(entity, dict):
            new_related.append(entity)
            continue
        cloned = deepcopy(entity)
        _apply_owns_check(cloned, warnings)
        new_related.append(cloned)

    return (
        AssembledOutput(
            root_entity=new_root if new_root is not None else assembled.root_entity,
            related_entities=new_related,
            excluded_entities=list(assembled.excluded_entities),
            warnings=list(assembled.warnings),
        ),
        warnings,
    )


__all__ = ["validate_ownership"]
