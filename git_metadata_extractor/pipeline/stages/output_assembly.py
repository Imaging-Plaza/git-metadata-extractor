from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.pipeline.stages.models import AssembledOutput, ReconciledEntities

if TYPE_CHECKING:
    from git_metadata_extractor.validation.schema_validation import (
        BatchValidationResult,
        ValidationResult,
    )

ROOT_ENTITY_PRIORITY = (
    "repository",
    "person",
    "organization",
    "article",
)
PLURAL_ENTITY_KEY_BY_SINGULAR = {
    "repository": "repositories",
    "person": "persons",
    "organization": "organizations",
    "article": "articles",
    "membership": "memberships",
    "contribution": "contributions",
}
SINGULAR_ENTITY_KEY_BY_PLURAL = {
    plural: singular
    for singular, plural in PLURAL_ENTITY_KEY_BY_SINGULAR.items()
}
JSON_ENTITY_BUCKET_BY_TYPE = {
    "schema:SoftwareSourceCode": "repositories",
    "schema:Person": "persons",
    "org:Organization": "organizations",
    "schema:ScholarlyArticle": "articles",
    "org:Membership": "memberships",
    "pulse:Contribution": "contributions",
}


@dataclass(slots=True)
class RootEntityValidationError(ValueError):
    status_code: int = 422
    entity_type: str = ""
    entity_id: str = ""
    validation_errors: list[dict[str, str]] = field(default_factory=list)


def _entity_singular_type(entity_type: str) -> str:
    normalized = entity_type.strip().lower()
    return SINGULAR_ENTITY_KEY_BY_PLURAL.get(normalized, normalized)


def _entity_identity(entity_type: str, payload: dict[str, Any]) -> tuple[str, str | None]:
    return _entity_singular_type(entity_type), payload.get("id") if isinstance(payload.get("id"), str) else None


def _select_root_entity(
    entities: dict[str, list[dict[str, Any]]],
    *,
    root_entity_type: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if root_entity_type is not None:
        normalized_root = _entity_singular_type(root_entity_type)
        plural_key = PLURAL_ENTITY_KEY_BY_SINGULAR.get(normalized_root)
        if plural_key is not None:
            candidates = entities.get(plural_key, [])
            if candidates:
                return normalized_root, candidates[0]
        message = (
            "Unable to assemble output without a root "
            f"{normalized_root} entity"
        )
        raise ValueError(message)

    for root_singular in ROOT_ENTITY_PRIORITY:
        plural_key = PLURAL_ENTITY_KEY_BY_SINGULAR[root_singular]
        candidates = entities.get(plural_key, [])
        if candidates:
            return root_singular, candidates[0]
    message = "Unable to assemble output without a root entity"
    raise ValueError(message)


def _clean_relationship_refs(entity: dict[str, Any], excluded_ids: set[str]) -> dict[str, Any]:
    cleaned = deepcopy(entity)
    for key, value in list(cleaned.items()):
        if isinstance(value, list):
            cleaned[key] = [
                item
                for item in value
                if not (isinstance(item, str) and item in excluded_ids)
            ]
        elif isinstance(value, str) and value in excluded_ids:
            cleaned[key] = None
    return cleaned


def _entities_by_type(entities: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        "repositories": [],
        "persons": [],
        "organizations": [],
        "articles": [],
        "memberships": [],
        "contributions": [],
    }
    for entity in entities:
        entity_type = entity.get("type")
        if not isinstance(entity_type, str):
            continue
        bucket = JSON_ENTITY_BUCKET_BY_TYPE.get(entity_type)
        if bucket is None:
            continue
        grouped[bucket].append(deepcopy(entity))
    return grouped


def build_json_output(assembled: AssembledOutput) -> dict[str, Any]:
    graph_entities: list[dict[str, Any]] = []
    if isinstance(assembled.root_entity, dict):
        graph_entities.append(assembled.root_entity)
    graph_entities.extend(assembled.related_entities)
    return {
        "root_entity": deepcopy(assembled.root_entity),
        "related_entities": deepcopy(assembled.related_entities),
        "excluded_entities": deepcopy(assembled.excluded_entities),
        "entities_by_type": _entities_by_type(graph_entities),
    }


def assemble_output(  # noqa: C901, PLR0912
    reconciled: ReconciledEntities,
    strict_results: BatchValidationResult,
    *,
    root_entity_type: str | None = None,
) -> AssembledOutput:
    root_entity_type, root_entity = _select_root_entity(
        reconciled.entities,
        root_entity_type=root_entity_type,
    )

    invalid_details: dict[tuple[str, str], ValidationResult] = {}
    invalid_entities_by_identity: dict[tuple[str, str], tuple[str, dict[str, Any], ValidationResult]] = {}
    for invalid_entity_type, invalid_payload, invalid_validation in strict_results.invalid_entities:
        singular_type, entity_id = _entity_identity(invalid_entity_type, invalid_payload)
        if entity_id is None:
            continue
        key = (singular_type, entity_id)
        invalid_details[key] = invalid_validation
        invalid_entities_by_identity[key] = (
            singular_type,
            invalid_payload,
            invalid_validation,
        )

    root_entity_singular, root_entity_id = _entity_identity(root_entity_type, root_entity)
    root_identity: tuple[str, str] | None = None
    if root_entity_id is not None:
        root_identity = (root_entity_singular, root_entity_id)

    if root_identity is not None and root_identity in invalid_details:
        root_validation = invalid_details[root_identity]
        raise RootEntityValidationError(
            entity_type=root_entity_type,
            entity_id=root_identity[1],
            validation_errors=list(root_validation.errors),
        )

    excluded_entities: list[dict[str, Any]] = []
    excluded_ids: set[str] = set()
    for (entity_type, entity_id), (_, payload, validation) in invalid_entities_by_identity.items():
        if entity_id is None:
            continue
        if root_identity is not None and (entity_type, entity_id) == root_identity:
            continue
        excluded_ids.add(entity_id)
        excluded_entities.append(
            {
                "entity_type": entity_type,
                "entity": deepcopy(payload),
                "reason": list(validation.errors),
            },
        )

    related_entities: list[dict[str, Any]] = []
    for plural_type, entries in reconciled.entities.items():
        singular_type = _entity_singular_type(plural_type)
        for entry in entries:
            if entry is root_entity:
                continue
            entity_id = entry.get("id") if isinstance(entry.get("id"), str) else None
            if entity_id is not None and (singular_type, entity_id) in invalid_entities_by_identity:
                continue
            related_entities.append(deepcopy(entry))

    for singular_type, entries in (
        ("membership", reconciled.memberships),
        ("contribution", reconciled.contributions),
    ):
        for entry in entries:
            entity_id = entry.get("id") if isinstance(entry.get("id"), str) else None
            if entity_id is not None and (singular_type, entity_id) in invalid_entities_by_identity:
                continue
            related_entities.append(deepcopy(entry))

    warnings = [*reconciled.link_warnings]
    for excluded_entity in excluded_entities:
        entity = excluded_entity["entity"]
        entity_id = entity.get("id")
        errors = excluded_entity["reason"]
        warnings.append(
            (
                f"Excluded {excluded_entity['entity_type']} entity '{entity_id}' due to strict "
                f"validation errors: {errors}"
            ),
        )

    # Final safety net: collapse duplicate `id`s into one entity body
    # each. The earlier reconciliation step computes the canonical id
    # but does not always merge the bodies — production audit observed
    # the same URN appearing 4 times in one repo's `@graph` with
    # different `pulse:OrganizationType` (CommunitySpace vs
    # SoftwareProject) and different `schema:name` capitalisations
    # (numtide / Numtide), violating the JSON-LD contract that a
    # subject IRI carries one body per graph.
    related_entities, dedup_warnings = _merge_duplicate_entities(related_entities)
    warnings.extend(dedup_warnings)

    # Person `schema:url` self-loop drop — final canonical-form sweep.
    # The LLM person agent's own guard runs BEFORE canonicalization
    # has rewritten `id`, so when the LLM emits
    # `"id": "<urn>", "schema:url": "https://github.com/X"` the
    # equality check fails at agent time. Downstream stages then
    # resolve `id` to the github URL and the self-loop materialises in
    # the final graph. Re-check here, after every id has been
    # canonicalised, and drop the redundant url. Production audit
    # observed 14 self-loops in `gabyx/pandoc` alone after the
    # agent-level fixes shipped.
    _drop_person_url_self_loops(related_entities)
    if isinstance(root_entity, dict):
        _drop_person_url_self_loops([root_entity])

    # Article ↔ Repo linkback. Production audit (Bug V) found
    # `schema:citation` literally `null` in 441/441 repos, including
    # the 19 repos that successfully extracted ``schema:ScholarlyArticle``
    # entities via the RAG path. The articles existed in the @graph
    # with valid DOIs and authors but were never wired back to the
    # repository they describe, so a SPARQL like
    #
    #     ?repo schema:citation ?article . ?article a schema:ScholarlyArticle .
    #
    # returned nothing despite the data being present. Stamp the
    # article's DOI URL into the root repo's `schema:citation` list
    # so the cross-store link is materialised.
    cleaned_root = _clean_relationship_refs(root_entity, excluded_ids)
    cleaned_root, citation_warnings = _link_articles_to_root_repo(
        cleaned_root, related_entities,
    )
    warnings.extend(citation_warnings)

    return AssembledOutput(
        root_entity=cleaned_root,
        related_entities=related_entities,
        excluded_entities=excluded_entities,
        warnings=warnings,
    )


def _drop_person_url_self_loops(entities: list[dict[str, Any]]) -> None:
    """Mutate Persons in-place to drop `schema:url` self-loops.

    A self-loop is `schema:url == id` (in either the bare-string or
    `{"@id": ...}` form). Mutates the list in place since this stage
    already deep-copies upstream payloads.
    """
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        type_value = entity.get("type") or entity.get("@type")
        if isinstance(type_value, list):
            is_person = any("schema:Person" in str(t) for t in type_value)
        else:
            is_person = isinstance(type_value, str) and "schema:Person" in type_value
        if not is_person:
            continue
        pid = entity.get("id") or entity.get("@id")
        if not isinstance(pid, str):
            continue
        url = entity.get("schema:url")
        target: str | None = None
        if isinstance(url, str):
            target = url.strip()
        elif isinstance(url, dict):
            inner = url.get("@id") or url.get("id")
            if isinstance(inner, str):
                target = inner.strip()
        if target is not None and target == pid.strip():
            entity.pop("schema:url", None)


def _link_articles_to_root_repo(
    root_entity: Any,
    related_entities: list[dict[str, Any]],
) -> tuple[Any, list[str]]:
    """Stamp ScholarlyArticle DOIs onto the root repo's `schema:citation`.

    The article agent emits ``schema:ScholarlyArticle`` entities with
    DOI-shaped `@id`s but never updates the repo's `schema:citation`
    field — by the time articles are produced (mid-pipeline) the repo
    payload has already been frozen. We do the wiring here, at output
    assembly time, when both the repo (as the root) and the articles
    (in `related_entities`) are in hand together.

    Idempotent: existing `schema:citation` values are preserved; new
    article ids are appended only when not already present. Returns
    the (possibly-modified) root plus one warning per article wired.
    """
    if not isinstance(root_entity, dict):
        return root_entity, []
    if root_entity.get("type") != "schema:SoftwareSourceCode":
        return root_entity, []

    article_ids: list[str] = []
    for entity in related_entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") != "schema:ScholarlyArticle":
            continue
        article_id = entity.get("id") or entity.get("@id")
        if isinstance(article_id, str) and article_id.strip():
            article_ids.append(article_id.strip())

    if not article_ids:
        return root_entity, []

    existing = root_entity.get("schema:citation")
    citations: list[str] = []
    seen: set[str] = set()
    if isinstance(existing, str) and existing.strip():
        citations.append(existing.strip())
        seen.add(existing.strip())
    elif isinstance(existing, list):
        for value in existing:
            if isinstance(value, str) and value.strip() and value.strip() not in seen:
                citations.append(value.strip())
                seen.add(value.strip())
            elif isinstance(value, dict):
                inner = value.get("@id") or value.get("id")
                if isinstance(inner, str) and inner.strip() and inner.strip() not in seen:
                    citations.append(inner.strip())
                    seen.add(inner.strip())

    stamped: list[str] = []
    for article_id in article_ids:
        if article_id in seen:
            continue
        citations.append(article_id)
        seen.add(article_id)
        stamped.append(article_id)

    if not stamped:
        return root_entity, []

    # Normalise to a list — `schema:citation` is unbounded per
    # `schema.org`, and a list is the only shape that round-trips
    # through every consumer cleanly (JSON-LD framing, SHACL, the
    # downstream Oxigraph upload).
    root_entity["schema:citation"] = citations
    repo_id = root_entity.get("id") or root_entity.get("@id") or "<repo>"
    warnings = [
        f"output_assembly: stamped schema:citation on {repo_id} → {article_id} "
        "(was {!r}). Linking the orphan ScholarlyArticle back to its repository.".format(existing)
        for article_id in stamped
    ]
    return root_entity, warnings


def _merge_duplicate_entities(
    entities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Collapse entries sharing the same ``id`` into one, preferring the
    first occurrence's body and filling its missing fields from later
    copies.

    Merge policy:
    - Scalar field present in both → keep first (left-bias). The
      reconciliation step that already ran ordered entries by source
      priority, so the first copy is the trusted one.
    - Scalar field present in only one → keep the present value.
    - List field present in both → concatenate + dedupe preserving order.
    - Dict field present in both → recursive merge with the same rules.

    Returns the deduped list and a warnings list summarising the
    collapses (one line per duplicate cluster).
    """
    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    duplicates: dict[str, int] = {}
    no_id: list[dict[str, Any]] = []

    for entity in entities:
        entity_id = entity.get("id") if isinstance(entity, dict) else None
        if not isinstance(entity_id, str) or not entity_id:
            no_id.append(entity)
            continue
        if entity_id not in seen:
            seen[entity_id] = deepcopy(entity)
            order.append(entity_id)
            continue
        _merge_into(seen[entity_id], entity)
        duplicates[entity_id] = duplicates.get(entity_id, 1) + 1

    merged = [seen[eid] for eid in order] + no_id
    warnings = [
        f"output_assembly: merged {count} entities sharing id={eid!r} into one body"
        for eid, count in sorted(duplicates.items())
    ]
    return merged, warnings


def _merge_into(target: dict[str, Any], source: Any) -> None:
    """Mutate ``target`` to fill in fields from ``source`` (left-bias)."""

    if not isinstance(source, dict):
        return
    # `_stub` marks a reference-only placeholder. A merged entity is a stub only
    # if BOTH sides were stubs — never let a placeholder's `_stub` bleed onto a
    # fuller same-id entity (Bug 07: stub appearing on fully-populated entities).
    source_stub = bool(source.get("_stub"))
    for key, value in source.items():
        if key == "_stub":
            continue
        if key not in target or target[key] is None or target[key] == [] or target[key] == {}:
            target[key] = deepcopy(value)
            continue
        existing = target[key]
        if isinstance(existing, list) and isinstance(value, list):
            seen_serialised: set[str] = set()
            combined: list[Any] = []
            for item in [*existing, *value]:
                token = json.dumps(item, sort_keys=True, default=str)
                if token in seen_serialised:
                    continue
                seen_serialised.add(token)
                combined.append(item)
            target[key] = combined
        elif isinstance(existing, dict) and isinstance(value, dict):
            _merge_into(existing, value)
    if target.get("_stub") and not source_stub:
        target.pop("_stub", None)
