from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.v2.pipeline.stages.models import AssembledOutput, ReconciledEntities

if TYPE_CHECKING:
    from src.v2.validation.schema_validation import (
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

    return AssembledOutput(
        root_entity=_clean_relationship_refs(root_entity, excluded_ids),
        related_entities=related_entities,
        excluded_entities=excluded_entities,
        warnings=warnings,
    )
