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
        "pulse:ror": ror_id,
        "pulse:githubOrganizationHandle": None,
        "pulse:infoscienceOrganizationIdentifier": None,
        "pulse:OrganizationType": None,
        "pulse:githubOrgFollowers": None,
        "org:hasUnit": [],
        "org:unitOf": None,
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

    Mitigation: when a repository has empty `schema:author`, look at the
    repo's GitHub `<owner>` handle (parsed from `pulse:githubRepositoryHandle`
    or its `id` URL). If a Person or Organization with the same handle
    exists in the reconciled graph, stamp its `id` into `schema:author`.
    Always emits a warning so this stays visible in the output.

    Mutates a copy; the original `reconciled` is unchanged.
    """

    repositories = reconciled.entities.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        return reconciled, []

    owner_index = _index_owners_and_orgs_by_handle(reconciled)
    warnings: list[str] = []
    new_repos: list[dict[str, Any]] = []
    changed = False

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
        if owner_handle and owner_handle in owner_index:
            owner_entity = owner_index[owner_handle]
            owner_id = owner_entity.get("id")
            if isinstance(owner_id, str) and owner_id:
                cloned["schema:author"] = [owner_id]
                changed = True
                warnings.append(
                    "KNOWN BUG (schema:author empty after reconciliation): "
                    f"stamped fallback owner '{owner_id}' as schema:author on "
                    f"{repo_id} (handle '{owner_handle}'). The LLM repo agent "
                    "likely emitted a contributor reference that reconciliation "
                    "couldn't canonicalize.",
                )
                new_repos.append(cloned)
                continue
        # No fallback available — leave the empty array in place. Strict
        # validation will still reject the root, but at least the warning
        # tells the user *why*.
        warnings.append(
            "KNOWN BUG (schema:author empty after reconciliation): could not "
            f"recover an owner fallback for {repo_id} (handle "
            f"'{owner_handle or '<none>'}'). Strict validation will reject "
            "this root.",
        )
        new_repos.append(cloned)

    if not changed and not warnings:
        return reconciled, []

    new_entities = {bucket: list(value) for bucket, value in reconciled.entities.items()}
    new_entities["repositories"] = new_repos
    new_reconciled = ReconciledEntities(
        entities=new_entities,
        memberships=list(reconciled.memberships),
        contributions=list(reconciled.contributions),
        link_warnings=list(reconciled.link_warnings),
        reconciliation_debug=dict(reconciled.reconciliation_debug),
    )
    return new_reconciled, warnings


__all__ = [
    "guarantee_repo_author",
    "infer_github_handle_parents",
    "infer_org_units",
    "infer_owners",
    "validate_ownership",
]
