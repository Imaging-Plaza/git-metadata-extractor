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
import uuid
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from src.v2.pipeline.stages.models import AssembledOutput, ReconciledEntities

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

    # Second pass: enforce inverse consistency on `pulse:owns`. When a
    # repo's `pulse:ownedBy` points at one Org but a DIFFERENT Org
    # (typically the same legal entity, dual-identity: github-handle
    # Org + ROR Org) carries the repo in its `pulse:owns`, drop the
    # spurious entry. Production audit (ENAC-CNPA, 171 cases) showed
    # the ROR-side Org keeping `pulse:owns: [repo]` while the repo
    # only pointed back to the github-side Org — a broken inverse
    # relationship that confuses downstream consumers.
    _enforce_owns_inverse_consistency(new_root, new_related, warnings)

    return (
        AssembledOutput(
            root_entity=new_root if new_root is not None else assembled.root_entity,
            related_entities=new_related,
            excluded_entities=list(assembled.excluded_entities),
            warnings=list(assembled.warnings),
        ),
        warnings,
    )


def _enforce_owns_inverse_consistency(
    root: dict[str, Any] | None,
    related: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    """Drop `pulse:owns` entries whose repo's `pulse:ownedBy` points elsewhere."""

    candidates: list[dict[str, Any]] = []
    if isinstance(root, dict):
        candidates.append(root)
    candidates.extend(e for e in related if isinstance(e, dict))

    # Build repo_id → ownedBy target lookup.
    repo_owned_by: dict[str, str] = {}
    for entity in candidates:
        if entity.get("type") != REPOSITORY_TYPE:
            continue
        repo_id = entity.get("id")
        if not isinstance(repo_id, str) or not repo_id:
            continue
        owned_by = entity.get(OWNED_BY_KEY)
        if isinstance(owned_by, dict):
            target = owned_by.get("@id")
            if isinstance(target, str) and target:
                repo_owned_by[repo_id] = target
        elif isinstance(owned_by, str) and owned_by.startswith(("http://", "https://", "urn:")):
            repo_owned_by[repo_id] = owned_by

    # For each org/person carrying `pulse:owns`, drop entries whose
    # repo points at a different `pulse:ownedBy`.
    for entity in candidates:
        owns = entity.get(OWNS_KEY)
        if not isinstance(owns, list) or not owns:
            continue
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id:
            continue
        kept: list[Any] = []
        for entry in owns:
            target = entry.get("@id") if isinstance(entry, dict) else entry
            if not isinstance(target, str) or not target:
                kept.append(entry)
                continue
            actual_owner = repo_owned_by.get(target)
            if actual_owner is None:
                # Repo not present in the graph or has no ownedBy — keep,
                # we have no signal to refute the claim.
                kept.append(entry)
                continue
            if actual_owner == entity_id:
                kept.append(entry)
                continue
            warnings.append(
                f"Dropped pulse:owns entry on {entity_id} → {target}: repo's "
                f"pulse:ownedBy points at {actual_owner!r}, not at this entity "
                "(dual-identity org with broken inverse).",
            )
        entity[OWNS_KEY] = kept if kept else None


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


def _owner_handle_for_repo_original_case(entity: dict[str, Any]) -> str | None:
    """Return the repo's github `<owner>` preserving the user's chosen case.

    Used when the handle is stamped onto new entities (e.g. a synthesized
    Person stub) where canonical casing matters for the resulting URL/name.
    """
    owner = _extract_owner_from_repo_handle(entity.get(REPO_HANDLE_KEY))
    if owner:
        return owner
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        owner = _extract_owner_from_repo_handle(identifiers.get(REPO_HANDLE_KEY))
        if owner:
            return owner
    return _extract_github_owner_from_url(entity.get("id") or entity.get("@id"))


def _owner_handle_for_repo(entity: dict[str, Any]) -> str | None:
    """Best-effort owner-handle lookup for a Repository entity (lower-cased).

    Lower-cased so it can match the index built by
    `_index_owners_and_orgs_by_handle`. For inserts that need canonical case,
    use `_owner_handle_for_repo_original_case`.
    """
    owner = _owner_handle_for_repo_original_case(entity)
    return owner.lower() if owner else None


def _index_owners(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a `handle.lower() -> entity` map for Person/Organization entities.

    When multiple entities advertise the same github handle (the
    typical case after reconciliation copies `pulse:githubOrganizationHandle`
    onto both the github-side Org and its ROR counterpart), prefer the
    one whose `id` is the canonical github URL. The github-side entity
    IS the direct owner of the repository; the ROR-side entity is an
    indirect/legal-entity parent linked via `org:hasUnit`. Production
    audit (Bug J, 165 cases on ENAC-CNPA et al.) showed `infer_owners`
    sometimes picking the ROR Org as the indexed owner, which then
    re-stamped `pulse:owns: [repo]` on the ROR side after
    `validate_ownership` had already cleaned it — leaving the inverse
    broken in the final graph.
    """
    index: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        handle = _entity_owner_handle(entity)
        if not handle:
            continue
        current = index.get(handle)
        if current is None:
            index[handle] = entity
            continue
        # Tie-break: prefer the github-URL-IRI entity over a ROR-IRI
        # entity for the same handle.
        if _id_is_github_url(entity) and not _id_is_github_url(current):
            index[handle] = entity
    return index


def _id_is_github_url(entity: dict[str, Any]) -> bool:
    """Return True when the entity's `id` is a `https://github.com/...` URL."""
    iid = entity.get("id") or entity.get("@id")
    return isinstance(iid, str) and iid.startswith(("https://github.com/", "http://github.com/"))


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
        # A bare-login string (e.g. "AlexanderBaltaian") emitted by the
        # repository agent is treated as missing — the JSON-LD context
        # types `pulse:ownedBy` as `@id`, so a bare token resolves to a
        # `<file:///CWD/{token}>` URI under SHACL. Only IRI-shaped values
        # are kept.
        existing_owned_by = entity.get(OWNED_BY_KEY)
        existing_is_bare_handle = (
            isinstance(existing_owned_by, str)
            and bool(existing_owned_by)
            and not existing_owned_by.startswith(("http://", "https://", "urn:"))
        )
        if existing_owned_by in (None, "", {}) or existing_is_bare_handle:
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

    # Final sweep: any repository that still carries a plain-string
    # `pulse:ownedBy` (because no matching owner entity was found in the
    # graph — typical of solo-user repos) is coerced to IRI shape and a
    # minimal Person stub is materialized when the target is a github
    # user URL with no matching Person/Org in the graph. Without the stub,
    # SHACL fails because `pulse:ownedBy` requires the target to be a
    # `schema:Person` or `org:Organization`.
    for entity in new_candidates:
        if entity.get("type") != REPOSITORY_TYPE:
            continue
        value = entity.get(OWNED_BY_KEY)
        if not isinstance(value, str) or not value:
            continue
        if value.startswith(("http://", "https://", "urn:")):
            entity[OWNED_BY_KEY] = {"@id": value}
        else:
            entity[OWNED_BY_KEY] = {"@id": f"https://github.com/{value}"}

    # Materialize Person stubs for github-user `pulse:ownedBy` targets that
    # have no matching Person/Organization in the graph. These are typically
    # solo-author repos where the owner never produced a contributor record.
    existing_ids = {
        e.get("id") or e.get("@id")
        for e in new_candidates
        if isinstance(e, dict) and (e.get("id") or e.get("@id"))
    }
    new_stubs: list[dict[str, Any]] = []
    seen_stubs: set[str] = set()
    for entity in new_candidates:
        if entity.get("type") != REPOSITORY_TYPE:
            continue
        value = entity.get(OWNED_BY_KEY)
        if not isinstance(value, dict):
            continue
        target_id = value.get("@id")
        if not isinstance(target_id, str) or not target_id:
            continue
        if target_id in existing_ids or target_id in seen_stubs:
            continue
        if not target_id.startswith("https://github.com/"):
            continue
        handle = target_id.removeprefix("https://github.com/").strip("/").split("/")[0]
        if not handle:
            continue
        stub_uuid = str(uuid4())
        new_stubs.append(
            {
                "id": target_id,
                "type": "schema:Person",
                "shacl": "pulse:PersonShape",
                "identifiers": {
                    "pulse:githubUsername": handle,
                    "uuid": stub_uuid,
                },
                "idSource": "pulse:githubUsername",
                "schema:name": handle,
                "pulse:githubUsername": handle,
            },
        )
        seen_stubs.add(target_id)
        warnings.append(
            f"Inferred minimal Person stub for github owner '{handle}' "
            f"({target_id}) referenced by repository {entity.get('id')}.",
        )
    if new_stubs:
        new_related = list(new_related) + new_stubs

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
    """Set `org:unitOf` on child only if currently empty. Returns True if set.

    `org:unitOf` is a list of parent IDs (multi-parent allowed by SHACL). This
    helper preserves the historical "never overwrite an existing value"
    semantics: it only stamps when the list is empty / missing / null.
    """
    existing = child.get(UNIT_OF_KEY)
    if existing in (None, "", {}, []):
        child[UNIT_OF_KEY] = [parent_id]
        return True
    if isinstance(existing, list):
        for entry in existing:
            if (isinstance(entry, dict) and entry.get("@id") == parent_id) or entry == parent_id:
                return False
        return False
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


# Tokens we drop when extracting query terms from a github handle. These are
# generic enough that searching for them on ROR gives mostly noise.
_GITHUB_HANDLE_GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "lab",
        "labs",
        "team",
        "group",
        "org",
        "project",
        "projects",
        "research",
        "code",
        "open",
        "source",
        "data",
        "science",  # too generic on its own; "data science" or "swiss data science" is fine
        "center",
        "centre",
        "institute",
        "institut",
        "the",
        "an",
        "and",
        "of",
        "for",
        "in",
        "at",
    },
)


def _github_handle_query_terms(handle: str, name: str | None) -> list[str]:
    """Build candidate ROR query strings from a github handle + display name.

    Strategy:
    1. Use the display name as-is when it's longer than the handle (more useful for ROR).
    2. Use the full handle (e.g. `SwissDataScienceCenter`) as a single query.
    3. Tokenize the handle on `-`/`_`, drop generic + short tokens.
    4. Return the most distinctive 2-3 tokens.

    Cap is intentional — each query becomes a ROR API call, so we trade recall
    for cost.
    """

    queries: list[str] = []

    name = (name or "").strip()
    handle = handle.strip()
    if name and name.lower() != handle.lower() and len(name) >= 3:
        queries.append(name)

    if handle and handle not in queries:
        queries.append(handle)

    # Tokenize handle on -/_, keep the meaningful parts.
    tokens = [
        part.lower()
        for part in re.split(r"[-_]+", handle)
        if len(part) >= 3 and part.lower() not in _GITHUB_HANDLE_GENERIC_TOKENS
    ]
    # Add the longest tokens first (most distinctive).
    tokens.sort(key=lambda token: -len(token))
    for token in tokens[:2]:
        if token not in {q.lower() for q in queries}:
            queries.append(token)

    return queries


def _ror_record_score(
    *,
    handle: str,
    name: str | None,
    ror_record: dict[str, Any],
) -> int:
    """Heuristic match score between a github org and a ROR record.

    Counts overlap between the github org's tokens (handle + display name)
    and the ROR org's name + aliases + acronyms. Higher = better.
    """

    gh_tokens = set(_handle_aliases(handle))
    if isinstance(name, str):
        gh_tokens.update(_name_aliases(name))
    if not gh_tokens:
        return 0

    ror_tokens: set[str] = set()
    ror_tokens.update(_name_aliases(ror_record.get("name")))
    aliases = ror_record.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            ror_tokens.update(_name_aliases(alias))
    acronyms = ror_record.get("acronyms")
    if isinstance(acronyms, list):
        for acronym in acronyms:
            if isinstance(acronym, str):
                ror_tokens.add(acronym.lower())

    return len(gh_tokens & ror_tokens)


def _build_minimal_ror_org(ror_record: dict[str, Any]) -> dict[str, Any] | None:
    """Construct a minimal `org:Organization` entity from a ROR search hit.

    Returns None if the record lacks an id or name (can't be a useful org).
    """

    ror_id = ror_record.get("id")
    name = ror_record.get("name")
    if not isinstance(ror_id, str) or not ror_id or not isinstance(name, str) or not name:
        return None
    return {
        "id": ror_id,
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {
            "pulse:ror": ror_id,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": None,
            "uuid": str(uuid4()),
        },
        "idSource": "pulse:ror",
        "schema:name": name,
        # The v2.1.2 OrganizationShape requires at least one of
        # schema:identifier, pulse:githubOrganizationHandle, or
        # pulse:infoscienceOrganizationIdentifier. Surface the ROR id as
        # schema:identifier so the stub satisfies the constraint without
        # needing additional enrichment.
        "schema:identifier": ror_id,
        "pulse:ror": ror_id,
        "pulse:githubOrganizationHandle": None,
        "pulse:infoscienceOrganizationIdentifier": None,
        "pulse:OrganizationType": None,
        "pulse:githubOrgFollowers": None,
        "org:hasUnit": [],
        "org:unitOf": [],
        "pulse:owns": [],
    }


def infer_github_handle_parents(
    assembled: AssembledOutput,
    *,
    providers: Any = None,
    max_candidates_per_handle: int = 5,
    min_match_score: int = 1,
) -> tuple[AssembledOutput, list[str]]:
    """For every github-only org in the graph, fuzzy-search ROR for matching
    parent organizations and add them to the graph.

    The github org always remains as a standalone entity (its `id` and other
    fields are untouched). The fuzzy matching:

    1. Builds 1-3 query strings from the github org's handle + display name
       (full name, full handle, top distinctive tokens).
    2. Calls `providers.ror.search_organizations(query)` for each (cached).
    3. Scores each ROR hit by token overlap against the github org's
       handle + name + aliases + acronyms.
    4. Adds candidate ROR records with score ≥ `min_match_score` to the
       graph as minimal entities (only id, ROR id, and canonical name —
       leaves the rest null and lets downstream stages / agents enrich).
    5. Picks the highest-scoring candidate as the github org's `unitOf`
       parent and stamps the reciprocal `hasUnit`.

    No-ops gracefully when `providers.ror` is None — the github orgs stay
    in the graph untouched.

    `max_candidates_per_handle` caps how many ROR matches per github org
    we add to the graph (top-K by score). `min_match_score` is the
    minimum token-overlap to count a hit as plausible.

    Never overwrites an existing `org:unitOf` value, and reuses an already-
    in-graph ROR entity rather than duplicating.
    """

    ror_provider = getattr(providers, "ror", None) if providers is not None else None
    if ror_provider is None:
        return assembled, []

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
    # Index existing orgs so we don't duplicate a ROR entity that's already
    # been brought into the graph (either by an upstream stage or by a
    # previous github org in this same loop).
    by_id: dict[str, dict[str, Any]] = {}
    by_ror: dict[str, dict[str, Any]] = {}
    for org in organizations:
        org_id = org.get("id")
        if isinstance(org_id, str) and org_id:
            by_id.setdefault(org_id, org)
        ror = _entity_ror_id(org)
        if isinstance(ror, str) and ror:
            by_ror.setdefault(ror, org)

    warnings: list[str] = []
    inserted: dict[str, dict[str, Any]] = {}

    for org in organizations:
        # Skip orgs that already have a ROR id (they're already canonical).
        if _entity_ror_id(org) is not None:
            continue
        handle = _entity_github_org_handle(org)
        if not handle:
            continue
        org_id = org.get("id") if isinstance(org.get("id"), str) else None
        if not org_id:
            continue
        org_name = org.get("schema:name") if isinstance(org.get("schema:name"), str) else None

        # Build query terms then search ROR. Cache hits make repeated runs cheap.
        queries = _github_handle_query_terms(handle, org_name)
        if not queries:
            continue
        scored: dict[str, tuple[int, dict[str, Any]]] = {}
        for query in queries:
            try:
                hits = ror_provider.search_organizations(query)
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    f"github_handle_parents: ROR search failed for query "
                    f"'{query}' (handle '{handle}'): {exc}",
                )
                continue
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                ror_id = hit.get("id")
                if not isinstance(ror_id, str) or not ror_id:
                    continue
                score = _ror_record_score(
                    handle=handle,
                    name=org_name,
                    ror_record=hit,
                )
                if score < min_match_score:
                    continue
                # Keep the best score we've seen for this ROR id.
                existing = scored.get(ror_id)
                if existing is None or existing[0] < score:
                    scored[ror_id] = (score, hit)

        if not scored:
            continue

        # Top-K by score (highest first). The first one becomes the unitOf
        # parent; the rest are added as siblings.
        top_candidates = sorted(scored.items(), key=lambda item: -item[1][0])[
            :max_candidates_per_handle
        ]

        ranked_entities: list[dict[str, Any]] = []
        for ror_id, (score, hit) in top_candidates:
            existing_entity = by_ror.get(ror_id) or by_id.get(ror_id) or inserted.get(ror_id)
            if existing_entity is None:
                new_entity = _build_minimal_ror_org(hit)
                if new_entity is None:
                    continue
                inserted[ror_id] = new_entity
                by_id[ror_id] = new_entity
                by_ror[ror_id] = new_entity
                ranked_entities.append(new_entity)
                warnings.append(
                    f"Inserted ROR organization '{ror_id}' "
                    f"({hit.get('name')}) from github handle '{handle}' "
                    f"(score={score}).",
                )
            else:
                ranked_entities.append(existing_entity)

        # Best match becomes the parent.
        if not ranked_entities:
            continue
        parent_entity = ranked_entities[0]
        parent_id = parent_entity.get("id")
        if not isinstance(parent_id, str) or not parent_id:
            continue
        unit_of_set = _set_unit_of(org, parent_id)
        has_unit_set = _add_to_has_unit(parent_entity, org_id)
        if unit_of_set:
            warnings.append(
                f"Inferred org:unitOf on {org_id} → {parent_id} "
                f"(github handle '{handle}' fuzzy-matched ROR record "
                f"'{parent_entity.get('schema:name')}').",
            )
        if has_unit_set:
            warnings.append(f"Inferred org:hasUnit on {parent_id} → {org_id}.")

    # Append the newly-inserted ROR org entities to the related list.
    for ror_id, entity in inserted.items():
        new_related.append(entity)

    updated = AssembledOutput(
        root_entity=new_root if new_root is not None else assembled.root_entity,
        related_entities=new_related,
        excluded_entities=list(assembled.excluded_entities),
        warnings=list(assembled.warnings),
    )
    return updated, warnings


def _index_owners_and_orgs_by_handle(
    reconciled: ReconciledEntities,
) -> dict[str, dict[str, Any]]:
    """Build `lower-cased handle -> entity` for both Persons and Organizations
    sourced from the reconciled bucket dict (not from an `AssembledOutput`)."""

    index: dict[str, dict[str, Any]] = {}
    for bucket in ("persons", "organizations"):
        for entity in reconciled.entities.get(bucket, []) or []:
            if not isinstance(entity, dict):
                continue
            handle = _entity_owner_handle(entity)
            if handle:
                index.setdefault(handle, entity)
    return index


def _synthesize_owner_person_stub(handle: str) -> dict[str, Any]:
    """Build a minimal valid `schema:Person` from a github owner handle.

    Used by `guarantee_repo_author` when the repository's owner handle has no
    Person or Organization entity in the reconciled graph (typical of solo
    repos with no extracted contributors). The stub satisfies the strict
    Person schema so it survives validation and can be referenced as
    `schema:author` on the root repository.
    """
    profile_url = f"https://github.com/{handle}"
    return {
        "id": profile_url,
        "type": "schema:Person",
        "shacl": "pulse:PersonShape",
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": handle,
            "uuid": str(uuid4()),
        },
        "idSource": "pulse:githubUsername",
        "schema:name": handle,
        # `schema:url` intentionally left None — for github-only synthesized
        # stubs the only candidate URL would be the github profile, which
        # IS the Person's `id`. Emitting it produces a tautological
        # self-loop (Bug P, 1505 cases in the production audit + 14 more
        # observed in `gabyx/pandoc` even after the agent-side fixes,
        # because the stubs are added downstream of those agents).
        "schema:url": None,
        "pulse:githubUsername": handle,
        "pulse:orcidIdentifier": None,
        "pulse:infosciencePersonIdentifier": None,
        "org:hasMembership": [],
        "pulse:hasContribution": [],
        "pulse:owns": [],
    }


def guarantee_repo_author(
    reconciled: ReconciledEntities,
) -> tuple[ReconciledEntities, list[str]]:
    """Salvage Repository entities whose `schema:author` array is empty.

    Known bug: occasionally the LLM repo agent emits `schema:author`
    references that reconciliation can't canonicalize, and after dropping
    the unresolvable references the array ends up empty. Strict validation
    requires `schema:author` to be **non-empty**, which would fail the
    entire extraction. This is a workaround, not a real fix — the real fix
    is to teach reconciliation to keep the LLM-emitted reference verbatim
    when it can't canonicalize.

    Mitigation, two-tier:
    1. If a Person or Organization with the same github handle as the repo's
       `<owner>` already exists in the graph, stamp its `id` into
       `schema:author`.
    2. Otherwise, synthesize a minimal `schema:Person` stub from the github
       owner handle, insert it into the persons bucket, and stamp its id.
       This recovers solo-user repos where no contributor data was extracted.

    Always emits a warning so the salvage stays visible in the output.

    Mutates a copy; the original `reconciled` is unchanged.
    """

    repositories = reconciled.entities.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        return reconciled, []

    owner_index = _index_owners_and_orgs_by_handle(reconciled)
    warnings: list[str] = []
    new_repos: list[dict[str, Any]] = []
    synthesized_persons: list[dict[str, Any]] = []
    synthesized_handles: set[str] = set()
    changed = False

    salvaged_contributions: list[dict[str, Any]] = []
    existing_contribution_ids = {
        contribution.get("id")
        for contribution in reconciled.contributions
        if isinstance(contribution, dict) and isinstance(contribution.get("id"), str)
    }

    def _stamp_owner_contribution(person_id: str, repo_id: str) -> None:
        """Emit a synthetic Contribution for the salvaged owner→repo pair.

        Production audit found 146/441 repos where the owner appeared in
        `schema:author` but had no matching `pulse:Contribution` edge —
        these are exactly the repos that hit this salvage path. The
        author edge is half a relationship; without the Contribution
        the graph reports who-but-not-how-much, which downstream
        aggregators see as inconsistent. Emit a minimal Contribution
        with `pulse:contributionCount=1` (the at-least-one-commit
        baseline a repo owner must logically have) and null dates so
        downstream consumers can recognise it as a baseline edge.
        """
        composite_id = f"{person_id}__{repo_id}"
        if composite_id in existing_contribution_ids:
            return
        salvaged_contributions.append(
            {
                "id": composite_id,
                "type": "pulse:Contribution",
                "shacl": "pulse:ContributionShape",
                "identifiers": {
                    "pulse:composite": composite_id,
                    "uuid": str(uuid.uuid4()),
                },
                "idSource": "pulse:composite",
                "pulse:contributionTo": repo_id,
                "pulse:contributionCount": 1,
                "pulse:firstContributionDate": None,
                "pulse:lastContributionDate": None,
                "schema:author": person_id,
            },
        )
        existing_contribution_ids.add(composite_id)

    for entity in repositories:
        if not isinstance(entity, dict):
            new_repos.append(entity)
            continue
        author_value = entity.get("schema:author")
        is_empty = (
            author_value is None
            or (isinstance(author_value, list) and len(author_value) == 0)
        )
        if not is_empty:
            new_repos.append(entity)
            continue

        repo_id = entity.get("id") if isinstance(entity.get("id"), str) else "<unknown>"
        owner_handle = _owner_handle_for_repo(entity)
        cloned = deepcopy(entity)
        owner_entity = owner_index.get(owner_handle) if owner_handle else None
        owner_is_person = (
            isinstance(owner_entity, dict)
            and owner_entity.get("type") == "schema:Person"
        )
        owner_is_org = (
            isinstance(owner_entity, dict)
            and owner_entity.get("type") == "org:Organization"
        )
        if owner_is_person:
            owner_id = owner_entity.get("id")
            if isinstance(owner_id, str) and owner_id:
                cloned["schema:author"] = [owner_id]
                changed = True
                _stamp_owner_contribution(owner_id, repo_id)
                warnings.append(
                    "KNOWN BUG (schema:author empty after reconciliation): "
                    f"stamped fallback owner '{owner_id}' as schema:author on "
                    f"{repo_id} (handle '{owner_handle}'). The LLM repo agent "
                    "likely emitted a contributor reference that reconciliation "
                    "couldn't canonicalize.",
                )
                new_repos.append(cloned)
                continue
        if owner_handle:
            handle_original_case = _owner_handle_for_repo_original_case(entity) or owner_handle
            # When the github account is already taken by an Organization
            # entity in the graph, the synthesized Person needs a distinct
            # id so the two don't collide. Use a urn-shaped id rooted at
            # the repository so the placeholder is uniquely scoped.
            if owner_is_org:
                stub_id = f"urn:pulse:repo-author:{handle_original_case}/{handle_original_case}"
                synthesis_key = f"org-owner:{owner_handle}"
            else:
                stub_id = f"https://github.com/{handle_original_case}"
                synthesis_key = owner_handle
            if synthesis_key not in synthesized_handles:
                stub = _synthesize_owner_person_stub(handle_original_case)
                if owner_is_org:
                    stub["id"] = stub_id
                    stub["idSource"] = "uuid"
                synthesized_persons.append(stub)
                synthesized_handles.add(synthesis_key)
            cloned["schema:author"] = [stub_id]
            changed = True
            _stamp_owner_contribution(stub_id, repo_id)
            if owner_is_org:
                warnings.append(
                    "KNOWN BUG (schema:author empty after reconciliation): "
                    f"github owner '{handle_original_case}' is an Organization "
                    "and SHACL requires schema:author targets to be Person — "
                    f"synthesized Person placeholder '{stub_id}' as schema:author "
                    f"on {repo_id}.",
                )
            else:
                warnings.append(
                    "KNOWN BUG (schema:author empty after reconciliation): "
                    f"synthesized owner Person stub '{stub_id}' as schema:author on "
                    f"{repo_id} (handle '{handle_original_case}'). No matching Person "
                    "or Organization existed in the graph — typical of solo-user "
                    "repositories with no extracted contributor data.",
                )
            new_repos.append(cloned)
            continue
        # Could not derive a github owner handle — leave the empty array.
        # Strict validation will still reject the root.
        warnings.append(
            "KNOWN BUG (schema:author empty after reconciliation): could not "
            f"derive a github owner handle for {repo_id}. Strict validation "
            "will reject this root.",
        )
        new_repos.append(cloned)

    if not changed and not warnings:
        return reconciled, []

    new_entities = {bucket: list(value) for bucket, value in reconciled.entities.items()}
    new_entities["repositories"] = new_repos
    if synthesized_persons:
        new_entities["persons"] = list(new_entities.get("persons") or []) + synthesized_persons
    new_contributions = list(reconciled.contributions) + salvaged_contributions
    new_reconciled = ReconciledEntities(
        entities=new_entities,
        memberships=list(reconciled.memberships),
        contributions=new_contributions,
        link_warnings=list(reconciled.link_warnings),
        reconciliation_debug=dict(reconciled.reconciliation_debug),
    )
    return new_reconciled, warnings


def _strip_github_props(entity: dict[str, Any]) -> list[str]:
    """Remove GitHub-derived properties from `entity`. Returns the keys cleared."""
    cleared: list[str] = []
    for key in ("pulse:githubOrgFollowers", "pulse:githubOrganizationHandle"):
        if entity.get(key) not in (None, ""):
            entity[key] = None
            cleared.append(key)
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        if isinstance(identifiers.get("pulse:githubOrganizationHandle"), str):
            identifiers["pulse:githubOrganizationHandle"] = None
            cleared.append("identifiers.pulse:githubOrganizationHandle")
    return cleared


def demote_github_props_to_units(  # noqa: C901
    assembled: AssembledOutput,
) -> tuple[AssembledOutput, list[str]]:
    """Move `pulse:githubOrgFollowers` / `pulse:githubOrganizationHandle` off
    ROR-id'd parents and onto the github-only unit they describe.

    Fixes the data-shape bug reported in Imaging-Plaza/git-metadata-extractor
    issue #29/#33: a ROR organization with `org:hasUnit → github_org` was
    carrying the unit's follower count and handle on the legal entity. ROR
    identifies a legal/research entity; GitHub-derived metrics belong on
    the GitHub presence (the unit), not on the parent.

    Two cases handled:

    1. **Existing unit, matching handle.** Parent has `org:hasUnit →
       github_url` whose child carries the same handle as the parent.
       Move follower count to the child (only if missing) and strip the
       GitHub-derived properties from the parent.

    2. **No matching unit — synthesize one.** Parent has a
       `pulse:githubOrganizationHandle` but no `org:hasUnit` child for
       that handle (audit example: `ror.org/02s376052` EPFL carrying
       `"GeoEnergyLab-EPFL"` with `hasUnit=[]`). Emit a minimal
       github-only `org:Organization` stub for the handle, stamp
       `org:unitOf` on the unit and `org:hasUnit` on the parent, and
       strip the GitHub-derived properties from the parent. The stub
       satisfies `pulse:OrganizationShape` (one of `schema:identifier`,
       `pulse:githubOrganizationHandle`, or
       `pulse:infoscienceOrganizationIdentifier` minCount 1 — we provide
       the github handle) plus `schema:name`.
    """
    new_root: dict[str, Any] | None = (
        deepcopy(assembled.root_entity)
        if isinstance(assembled.root_entity, dict)
        else None
    )
    new_related: list[Any] = [
        deepcopy(entity) if isinstance(entity, dict) else entity
        for entity in assembled.related_entities
    ]
    candidates: list[dict[str, Any]] = []
    if new_root is not None:
        candidates.append(new_root)
    candidates.extend(e for e in new_related if isinstance(e, dict))

    id_index: dict[str, dict[str, Any]] = {}
    for entity in candidates:
        if entity.get("type") != ORGANIZATION_TYPE:
            continue
        entity_id = entity.get("id")
        if isinstance(entity_id, str) and entity_id:
            id_index[entity_id] = entity

    warnings: list[str] = []
    synthesized_units: list[dict[str, Any]] = []

    for parent in candidates:
        if parent.get("type") != ORGANIZATION_TYPE:
            continue
        parent_id = parent.get("id")
        if not isinstance(parent_id, str) or not parent_id.startswith("https://ror.org/"):
            continue
        parent_handle = _entity_github_org_handle(parent)
        if parent_handle is None:
            continue

        # Pull the handle as the agent emitted it (preserve original case
        # for the synthesized unit's id and display name).
        raw_handle = parent.get("pulse:githubOrganizationHandle")
        if not isinstance(raw_handle, str) or not raw_handle.strip():
            identifiers = parent.get("identifiers")
            raw_handle = identifiers.get("pulse:githubOrganizationHandle") if isinstance(identifiers, dict) else None
        if not isinstance(raw_handle, str) or not raw_handle.strip():
            continue
        raw_handle = raw_handle.strip()

        matched_child: dict[str, Any] | None = None
        units = parent.get(HAS_UNIT_KEY)
        if isinstance(units, list):
            for unit_ref in units:
                unit_id = unit_ref.get("@id") if isinstance(unit_ref, dict) else unit_ref
                if not isinstance(unit_id, str) or not unit_id.startswith("https://github.com/"):
                    continue
                child = id_index.get(unit_id)
                if child is None:
                    continue
                child_handle = _entity_github_org_handle(child)
                if child_handle == parent_handle:
                    matched_child = child
                    break

        # Case 2: synthesize the unit if no matching child exists.
        if matched_child is None:
            synthesized_id = f"https://github.com/{raw_handle}"
            if synthesized_id in id_index:
                # Same URL exists as a non-unit org (e.g. it wasn't in
                # parent's hasUnit list yet). Reuse instead of duplicating.
                matched_child = id_index[synthesized_id]
            else:
                matched_child = {
                    "id": synthesized_id,
                    "type": ORGANIZATION_TYPE,
                    "shacl": "pulse:OrganizationShape",
                    "identifiers": {
                        "pulse:githubOrganizationHandle": raw_handle,
                        "uuid": str(uuid4()),
                    },
                    "idSource": "pulse:githubOrganizationHandle",
                    "schema:name": raw_handle,
                    "pulse:githubOrganizationHandle": raw_handle,
                    "org:unitOf": [parent_id],
                    "_stub": True,
                }
                synthesized_units.append(matched_child)
                id_index[synthesized_id] = matched_child
                warnings.append(
                    f"Synthesized github-only org unit {synthesized_id} for "
                    f"ROR parent {parent_id} (handle {raw_handle!r}). Issue #29/#33 "
                    f"extension: ROR carried the unit handle with no existing "
                    f"`org:hasUnit` link.",
                )
            _add_to_has_unit(parent, matched_child["id"])
            _set_unit_of(matched_child, parent_id)

        # Move follower count to the unit (never overwrite an existing value).
        parent_followers = parent.get("pulse:githubOrgFollowers")
        if isinstance(parent_followers, int) and not isinstance(
            matched_child.get("pulse:githubOrgFollowers"), int
        ):
            matched_child["pulse:githubOrgFollowers"] = parent_followers

        cleared = _strip_github_props(parent)
        if cleared:
            warnings.append(
                f"Demoted GitHub-derived properties from ROR parent {parent_id} "
                f"to unit {matched_child.get('id')} (handle '{parent_handle}'): "
                f"{', '.join(cleared)}.",
            )

    if synthesized_units:
        new_related = list(new_related) + synthesized_units

    return (
        AssembledOutput(
            root_entity=new_root if new_root is not None else assembled.root_entity,
            related_entities=new_related,
            excluded_entities=list(assembled.excluded_entities),
            warnings=list(assembled.warnings),
        ),
        warnings,
    )


def emit_fork_parent_stubs(
    assembled: AssembledOutput,
) -> tuple[AssembledOutput, list[str]]:
    """For every repo with `pulse:isForkOf = <github_url>` referencing an
    upstream not in the graph, emit minimal `schema:SoftwareSourceCode` +
    `schema:Person` stubs so the SHACL `sh:class schema:SoftwareSourceCode`
    constraint on `pulse:isForkOf` is satisfied.

    Without these stubs SHACL flags 3 violations per fork
    (`schema:name`, `schema:author`, `pulse:githubRepositoryHandle` — all
    minCount 1 on `pulse:RepositoryShape`). With them the graph conforms
    and downstream consumers get a "this is a fork of X" pointer they
    can dereference.

    The stub carries only the SHACL-required fields:
    SoftwareSourceCode → `schema:name`, `pulse:githubRepositoryHandle`,
    `schema:author`. Person → `schema:name`, `pulse:githubUsername`.
    `_stub = True` marks them as derived.
    """
    new_root: dict[str, Any] | None = (
        deepcopy(assembled.root_entity)
        if isinstance(assembled.root_entity, dict)
        else None
    )
    new_related: list[Any] = [
        deepcopy(entity) if isinstance(entity, dict) else entity
        for entity in assembled.related_entities
    ]
    candidates: list[dict[str, Any]] = []
    if new_root is not None:
        candidates.append(new_root)
    candidates.extend(e for e in new_related if isinstance(e, dict))

    existing_ids: set[str] = set()
    for entity in candidates:
        eid = entity.get("id") or entity.get("@id")
        if isinstance(eid, str) and eid:
            existing_ids.add(eid)

    warnings: list[str] = []
    new_stubs: list[dict[str, Any]] = []
    seen_stubs: set[str] = set()

    for entity in candidates:
        if entity.get("type") != REPOSITORY_TYPE:
            continue
        fork_of = entity.get("pulse:isForkOf")
        target = fork_of.get("@id") if isinstance(fork_of, dict) else fork_of
        if not isinstance(target, str) or not target.startswith("https://github.com/"):
            continue
        if target in existing_ids or target in seen_stubs:
            continue
        handle = target.removeprefix("https://github.com/").strip("/")
        if "/" not in handle:
            continue
        owner, repo_name = handle.split("/", maxsplit=1)
        if not (owner and repo_name):
            continue
        owner_url = f"https://github.com/{owner}"

        # Person stub (parent's owner). Skip if already present.
        if owner_url not in existing_ids and owner_url not in seen_stubs:
            new_stubs.append(
                {
                    "id": owner_url,
                    "type": "schema:Person",
                    "shacl": "pulse:PersonShape",
                    "identifiers": {
                        "pulse:githubUsername": owner,
                        "uuid": str(uuid4()),
                    },
                    "idSource": "pulse:githubUsername",
                    "schema:name": owner,
                    "pulse:githubUsername": owner,
                    "_stub": True,
                },
            )
            seen_stubs.add(owner_url)

        # SoftwareSourceCode stub for the fork parent.
        new_stubs.append(
            {
                "id": target,
                "type": REPOSITORY_TYPE,
                "shacl": "pulse:RepositoryShape",
                "identifiers": {
                    "pulse:githubRepositoryHandle": handle,
                    "uuid": str(uuid4()),
                },
                "idSource": "pulse:githubRepositoryHandle",
                "schema:name": repo_name,
                "pulse:githubRepositoryHandle": handle,
                "schema:author": [owner_url],
                "_stub": True,
            },
        )
        seen_stubs.add(target)
        warnings.append(
            f"Inferred minimal SoftwareSourceCode stub for fork parent "
            f"{target!r} referenced by {entity.get('id')}.",
        )

    if new_stubs:
        new_related = list(new_related) + new_stubs

    return (
        AssembledOutput(
            root_entity=new_root if new_root is not None else assembled.root_entity,
            related_entities=new_related,
            excluded_entities=list(assembled.excluded_entities),
            warnings=list(assembled.warnings),
        ),
        warnings,
    )


__all__ = [
    "demote_github_props_to_units",
    "emit_fork_parent_stubs",
    "guarantee_repo_author",
    "infer_github_handle_parents",
    "infer_org_units",
    "infer_owners",
    "validate_ownership",
]
