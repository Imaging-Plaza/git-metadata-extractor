from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.v2.pipeline.stages.models import AssembledOutput

ENTITY_URI_PREFIX = "urn:pulse:"
HELPER_ONLY_FIELDS = {"shacl", "identifiers", "idSource", "_person_ref"}


def _normalize_node_id(value: Any, *, index: int) -> str:
    if isinstance(value, str) and value:
        if value.startswith(("http://", "https://", "urn:")):
            return value
        return f"{ENTITY_URI_PREFIX}{value}"
    return f"{ENTITY_URI_PREFIX}node-{index}"


def _normalize_node_type(value: Any) -> str | list[str] | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        normalized = [item for item in value if isinstance(item, str) and item]
        if normalized:
            return normalized
    return None


def _iri_typed_context_terms(jsonld_context: dict[str, Any]) -> set[str]:
    iri_typed_terms: set[str] = set()
    for term, mapping in jsonld_context.items():
        if not isinstance(term, str) or not isinstance(mapping, dict):
            continue
        if mapping.get("@type") != "@id":
            continue
        iri_typed_terms.add(term)
        mapped_term = mapping.get("@id")
        if isinstance(mapped_term, str) and mapped_term:
            iri_typed_terms.add(mapped_term)
    return iri_typed_terms


def _normalize_jsonld_value(
    value: Any,
    *,
    id_map: dict[str, str],
    iri_typed_terms: set[str],
    current_property: str | None = None,
) -> Any:
    if isinstance(value, str):
        if isinstance(current_property, str) and current_property in iri_typed_terms:
            target_id = id_map.get(value)
            if target_id is not None:
                return {"@id": target_id}
        return value
    if isinstance(value, list):
        return [
            _normalize_jsonld_value(
                item,
                id_map=id_map,
                iri_typed_terms=iri_typed_terms,
                current_property=current_property,
            )
            for item in value
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized[key] = _normalize_jsonld_value(
                item,
                id_map=id_map,
                iri_typed_terms=iri_typed_terms,
                current_property=key if isinstance(key, str) else None,
            )
        return normalized
    return deepcopy(value)


def build_jsonld_output(
    *,
    assembled: AssembledOutput,
    jsonld_context: dict[str, Any],
) -> dict[str, Any]:
    entities: list[dict[str, Any]] = []
    if isinstance(assembled.root_entity, dict):
        entities.append(assembled.root_entity)
    entities.extend(assembled.related_entities)

    id_map: dict[str, str] = {}
    iri_typed_terms = _iri_typed_context_terms(jsonld_context)
    for index, entity in enumerate(entities):
        entity_id = entity.get("id")
        if isinstance(entity_id, str) and entity_id:
            id_map[entity_id] = _normalize_node_id(entity_id, index=index)

    graph: list[dict[str, Any]] = []
    for index, entity in enumerate(entities):
        node: dict[str, Any] = {}
        node_id = entity.get("id")
        node["@id"] = _normalize_node_id(node_id, index=index)
        node_type = _normalize_node_type(entity.get("type"))
        if node_type is not None:
            node["@type"] = node_type

        for key, value in entity.items():
            if key in HELPER_ONLY_FIELDS or key in {"id", "type"}:
                continue
            node[key] = _normalize_jsonld_value(
                value,
                id_map=id_map,
                iri_typed_terms=iri_typed_terms,
                current_property=key,
            )

        graph.append(node)

    graph.sort(key=lambda item: str(item.get("@id", "")))
    payload: dict[str, Any] = {
        "@context": deepcopy(jsonld_context),
        "@graph": graph,
    }
    if assembled.excluded_entities:
        payload["excluded_entities"] = deepcopy(assembled.excluded_entities)
    return payload
