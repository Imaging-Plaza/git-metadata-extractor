"""Deterministic ownership validation and inference for assembled output.

Three independent passes, all pure-function, no LLM, no network:

1. **Type check** (`validate_ownership`) — only `schema:Person` and
   `org:Organization` entities may carry `pulse:owns`. If any other entity
   type has `pulse:owns`, the field is stripped.
2. **GitHub-handle check** (`validate_ownership`) — for every `pulse:owns`
   entry that points at a `https://github.com/<owner>/<repo>` URL,
   `<owner>` must match the source entity's GitHub handle (Person:
   `pulse:githubUsername`; Organization: `pulse:githubOrganizationHandle`).
   Mismatched entries are dropped.
3. **Owner inference** (`infer_owners`) — for every Repository entity, parse
   the `<owner>` segment from `pulse:githubRepositoryHandle` or its `id`
   URL, find the matching Person/Organization by handle, and stamp both
   sides of the relationship: `pulse:ownedBy` on the repo and the repo's id
   into the entity's `pulse:owns`. Only fills gaps — never overwrites an
   existing populated `pulse:ownedBy` value.

Run all three between `apply_link_pruning_to_assembled_output` and
`build_jsonld_output` so SHACL sees the cleaned-up output.
"""
from __future__ import annotations

import re
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


REPOSITORY_TYPE = "schema:SoftwareSourceCode"
OWNED_BY_KEY = "pulse:ownedBy"
REPO_HANDLE_KEY = "pulse:githubRepositoryHandle"


def _extract_owner_from_repo_handle(handle: Any) -> str | None:
    """Return the `<owner>` segment of `<owner>/<repo>`, else `None`."""
    if not isinstance(handle, str):
        return None
    candidate = handle.strip()
    if "/" not in candidate:
        return None
    owner = candidate.split("/", maxsplit=1)[0].strip()
    return owner or None


def _owner_handle_for_repo(entity: dict[str, Any]) -> str | None:
    """Best-effort owner-handle lookup for a Repository entity.

    Tries `pulse:githubRepositoryHandle` first, then parses the github URL in
    `id` / `@id`. Returns the owner segment lower-cased.
    """
    owner = _extract_owner_from_repo_handle(entity.get(REPO_HANDLE_KEY))
    if owner:
        return owner.lower()
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        owner = _extract_owner_from_repo_handle(identifiers.get(REPO_HANDLE_KEY))
        if owner:
            return owner.lower()
    # Fall back to parsing the id URL
    owner = _extract_github_owner_from_url(entity.get("id") or entity.get("@id"))
    if owner:
        return owner.lower()
    return None


def _index_owners(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a `handle.lower() -> entity` map for Person and Organization entities."""
    index: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        handle = _entity_owner_handle(entity)
        if handle:
            index.setdefault(handle, entity)
    return index


def _add_to_owns(entity: dict[str, Any], repo_id: str) -> bool:
    """Append `{"@id": repo_id}` to `entity[OWNS_KEY]` (deduped). Returns True if added."""
    existing = entity.get(OWNS_KEY)
    if existing is None or existing == []:
        entity[OWNS_KEY] = [{"@id": repo_id}]
        return True
    if not isinstance(existing, list):
        # Shouldn't happen post-validate_ownership, but be defensive.
        return False
    for entry in existing:
        if isinstance(entry, dict) and entry.get("@id") == repo_id:
            return False
        if isinstance(entry, str) and entry == repo_id:
            return False
    existing.append({"@id": repo_id})
    return True


def infer_owners(
    assembled: AssembledOutput,
) -> tuple[AssembledOutput, list[str]]:
    """Stamp the deterministic owner relationship in both directions.

    For each `schema:SoftwareSourceCode` entity:
    - Parse the GitHub owner from `pulse:githubRepositoryHandle` or `id`.
    - If a Person/Organization with the matching `pulse:githubUsername` /
      `pulse:githubOrganizationHandle` exists in the graph, set
      `pulse:ownedBy` on the repo (only when currently null/missing) and add
      the repo's `id` to that entity's `pulse:owns`.

    Returns a copy of `assembled` with the relationships stamped, plus a list
    of human-readable warnings reporting each inferred link.
    """
    candidates: list[dict[str, Any]] = []
    if isinstance(assembled.root_entity, dict):
        candidates.append(assembled.root_entity)
    candidates.extend(
        entity for entity in assembled.related_entities if isinstance(entity, dict)
    )

    new_root: dict[str, Any] | None = (
        deepcopy(assembled.root_entity)
        if isinstance(assembled.root_entity, dict)
        else None
    )
    new_related: list[Any] = [
        deepcopy(entity) if isinstance(entity, dict) else entity
        for entity in assembled.related_entities
    ]
    new_candidates: list[dict[str, Any]] = []
    if new_root is not None:
        new_candidates.append(new_root)
    new_candidates.extend(e for e in new_related if isinstance(e, dict))

    owner_index = _index_owners(new_candidates)
    warnings: list[str] = []

    for entity in new_candidates:
        if entity.get("type") != REPOSITORY_TYPE:
            continue
        repo_id = entity.get("id")
        if not isinstance(repo_id, str) or not repo_id:
            continue
        owner_handle = _owner_handle_for_repo(entity)
        if owner_handle is None:
            continue
        owner_entity = owner_index.get(owner_handle)
        if owner_entity is None:
            continue
        owner_id = owner_entity.get("id")
        if not isinstance(owner_id, str) or not owner_id:
            continue

        # Stamp the inverse direction on the repo (only fill the gap).
        existing_owned_by = entity.get(OWNED_BY_KEY)
        if existing_owned_by in (None, "", {}):
            entity[OWNED_BY_KEY] = {"@id": owner_id}
            warnings.append(
                f"Inferred pulse:ownedBy on {repo_id} → {owner_id} "
                f"(handle '{owner_handle}').",
            )
        elif (
            isinstance(existing_owned_by, dict)
            and existing_owned_by.get("@id") == owner_id
        ) or existing_owned_by == owner_id:
            # Already correct; nothing to do.
            pass
        else:
            warnings.append(
                f"pulse:ownedBy on {repo_id} already set to "
                f"{existing_owned_by!r}; not overwriting with inferred {owner_id}.",
            )

        if _add_to_owns(owner_entity, repo_id):
            warnings.append(
                f"Inferred pulse:owns on {owner_id} → {repo_id} "
                f"(handle '{owner_handle}').",
            )

    return (
        AssembledOutput(
            root_entity=new_root if new_root is not None else assembled.root_entity,
            related_entities=new_related,
            excluded_entities=list(assembled.excluded_entities),
            warnings=list(assembled.warnings),
        ),
        warnings,
    )


ORGANIZATION_TYPE = "org:Organization"
ROR_IDENTIFIER_KEY = "pulse:ror"
UNIT_OF_KEY = "org:unitOf"
HAS_UNIT_KEY = "org:hasUnit"

# Common acronym/short-form anchors that organizations use to refer to a
# parent. We treat the parent's `schema:name` (or any token in it) as a
# candidate alias when matching against the github org's name/handle.
_TOKEN_PATTERN = re.compile(r"[A-Za-z]{2,}")


def _entity_ror_id(entity: dict[str, Any]) -> str | None:
    """Return the ROR id for an organization, or None."""
    direct = entity.get(ROR_IDENTIFIER_KEY)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        nested = identifiers.get(ROR_IDENTIFIER_KEY)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    schema_id = entity.get("schema:identifier")
    if isinstance(schema_id, str) and schema_id.strip().startswith("https://ror.org/"):
        return schema_id.strip()
    if isinstance(entity.get("id"), str) and entity["id"].startswith("https://ror.org/"):
        return entity["id"]
    return None


def _entity_github_org_handle(entity: dict[str, Any]) -> str | None:
    """Return the github organization handle (lowercased), or None."""
    direct = entity.get("pulse:githubOrganizationHandle")
    if isinstance(direct, str) and direct.strip():
        return direct.strip().lower()
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        nested = identifiers.get("pulse:githubOrganizationHandle")
        if isinstance(nested, str) and nested.strip():
            return nested.strip().lower()
    return None


def _name_aliases(value: Any) -> set[str]:
    """Extract lowercase alphabetic tokens (≥2 chars) from a name string."""
    if not isinstance(value, str):
        return set()
    return {match.group(0).lower() for match in _TOKEN_PATTERN.finditer(value)}


def _handle_aliases(handle: str) -> set[str]:
    """Tokenize a github handle by splitting on `-` and `_`."""
    return {part.lower() for part in re.split(r"[-_]+", handle) if len(part) >= 2}


def _set_unit_of(child: dict[str, Any], parent_id: str) -> bool:
    """Set `org:unitOf` on child only if currently null/missing. Returns True if set."""
    existing = child.get(UNIT_OF_KEY)
    if existing in (None, "", {}):
        child[UNIT_OF_KEY] = parent_id
        return True
    if (isinstance(existing, dict) and existing.get("@id") == parent_id) or existing == parent_id:
        return False
    return False


def _add_to_has_unit(parent: dict[str, Any], child_id: str) -> bool:
    """Append child_id to `org:hasUnit` (deduped). Returns True if added."""
    existing = parent.get(HAS_UNIT_KEY)
    if existing is None or existing == []:
        parent[HAS_UNIT_KEY] = [child_id]
        return True
    if not isinstance(existing, list):
        return False
    for entry in existing:
        if isinstance(entry, dict) and entry.get("@id") == child_id:
            return False
        if isinstance(entry, str) and entry == child_id:
            return False
    existing.append(child_id)
    return True


def infer_org_units(
    assembled: AssembledOutput,
) -> tuple[AssembledOutput, list[str]]:
    """Stamp `org:unitOf` / `org:hasUnit` between a github-only org and a
    ROR-backed parent when name evidence makes the relationship clear.

    Triggers only when:

    - There is exactly **one** ROR-backed organization in the graph (the
      candidate parent).
    - There is at least one organization with a github handle but **no**
      ROR (the candidate child).
    - The github handle (split on `-`/`_`) shares at least one ≥2-char
      alphabetic token with any token in the ROR org's `schema:name`.

    Conservative on purpose: a single ROR + a name-token overlap is strong
    enough to avoid coincidence (e.g. `EPFL-Open-Science` shares `epfl`
    with the ROR org `EPFL — École Polytechnique Fédérale de Lausanne`),
    but bails out cleanly when multiple ROR orgs exist or no overlap is
    found. Only fills gaps; never overwrites an existing `org:unitOf`.
    """
    candidates: list[dict[str, Any]] = []
    if isinstance(assembled.root_entity, dict):
        candidates.append(assembled.root_entity)
    candidates.extend(
        entity for entity in assembled.related_entities if isinstance(entity, dict)
    )

    new_root: dict[str, Any] | None = (
        deepcopy(assembled.root_entity)
        if isinstance(assembled.root_entity, dict)
        else None
    )
    new_related: list[Any] = [
        deepcopy(entity) if isinstance(entity, dict) else entity
        for entity in assembled.related_entities
    ]
    new_candidates: list[dict[str, Any]] = []
    if new_root is not None:
        new_candidates.append(new_root)
    new_candidates.extend(e for e in new_related if isinstance(e, dict))

    organizations = [
        entity for entity in new_candidates if entity.get("type") == ORGANIZATION_TYPE
    ]
    ror_orgs = [org for org in organizations if _entity_ror_id(org) is not None]
    github_only_orgs = [
        org
        for org in organizations
        if _entity_ror_id(org) is None and _entity_github_org_handle(org) is not None
    ]

    warnings: list[str] = []

    if len(ror_orgs) != 1 or not github_only_orgs:
        return (
            AssembledOutput(
                root_entity=new_root if new_root is not None else assembled.root_entity,
                related_entities=new_related,
                excluded_entities=list(assembled.excluded_entities),
                warnings=list(assembled.warnings),
            ),
            warnings,
        )

    parent = ror_orgs[0]
    parent_id = parent.get("id")
    if not isinstance(parent_id, str) or not parent_id:
        return (
            AssembledOutput(
                root_entity=new_root if new_root is not None else assembled.root_entity,
                related_entities=new_related,
                excluded_entities=list(assembled.excluded_entities),
                warnings=list(assembled.warnings),
            ),
            warnings,
        )

    parent_name_tokens = _name_aliases(parent.get("schema:name"))

    for child in github_only_orgs:
        child_id = child.get("id")
        handle = _entity_github_org_handle(child)
        if not isinstance(child_id, str) or not child_id or handle is None:
            continue
        handle_tokens = _handle_aliases(handle) | _name_aliases(child.get("schema:name"))
        overlap = handle_tokens & parent_name_tokens
        if not overlap:
            continue

        if _set_unit_of(child, parent_id):
            warnings.append(
                f"Inferred org:unitOf on {child_id} → {parent_id} "
                f"(matched on token{'s' if len(overlap) > 1 else ''} "
                f"{sorted(overlap)!r}).",
            )
        if _add_to_has_unit(parent, child_id):
            warnings.append(
                f"Inferred org:hasUnit on {parent_id} → {child_id}.",
            )

    return (
        AssembledOutput(
            root_entity=new_root if new_root is not None else assembled.root_entity,
            related_entities=new_related,
            excluded_entities=list(assembled.excluded_entities),
            warnings=list(assembled.warnings),
        ),
        warnings,
    )


__all__ = ["infer_org_units", "infer_owners", "validate_ownership"]
