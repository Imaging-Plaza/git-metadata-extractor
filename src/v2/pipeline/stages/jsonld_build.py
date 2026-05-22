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


_IRI_PREFIXES: tuple[str, ...] = ("http://", "https://", "urn:", "doi:")


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
            # External IRI (not in our graph): wrap in `{"@id": ...}` for
            # consistent shape across all iri-typed property values, so
            # downstream consumers don't have to handle two forms.
            if value.startswith(_IRI_PREFIXES):
                return {"@id": value}
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


def _drop_internal_keys(entity: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``entity`` without top-level `_`-prefixed keys."""
    return {
        key: value
        for key, value in entity.items()
        if not (isinstance(key, str) and key.startswith("_"))
    }


def build_jsonld_output(
    *,
    assembled: AssembledOutput,
    jsonld_context: dict[str, Any],
    include_internal_fields: bool = False,
) -> dict[str, Any]:
    """Build the JSON-LD `@graph` from a validated `AssembledOutput`.

    ``include_internal_fields=False`` (default) preserves the
    ontology-compliant behaviour: `_`-prefixed keys are stripped from
    **both** `@graph` nodes and `excluded_entities`, so the response
    contains zero `_` fields anywhere — only terms the open-pulse
    ontology declares.

    Set ``include_internal_fields=True`` to keep `_`-prefixed keys in
    the output (`@graph` and `excluded_entities` alike) — useful when
    the caller asked for the broader profile metadata (`_avatar_url`,
    `_bio`, `_company`, `_orcid_keywords`, `_dropped_affiliations`,
    etc.) that we collect but don't yet have ontology terms for.
    Strict SHACL validation has already run by this point (it always
    strips `_` fields), so flipping this flag is purely about what the
    consumer sees, not about validation.
    """

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
            if (
                not include_internal_fields
                and isinstance(key, str)
                and key.startswith("_")
            ):
                continue
            node[key] = _normalize_jsonld_value(
                value,
                id_map=id_map,
                iri_typed_terms=iri_typed_terms,
                current_property=key,
            )

        # Strip `pulse:ror` when redundant with the node's own `@id`. The
        # `org:Organization` SHACL shape is `sh:closed` and does not
        # declare `pulse:ror`; emitting the field on a ROR-id'd node
        # triggers a closed-shape violation. The `@id` is already the
        # ROR, so the field carries no additional information.
        node_iri = node.get("@id")
        if (
            isinstance(node_iri, str)
            and node.get("pulse:ror") == node_iri
        ):
            node.pop("pulse:ror", None)

        graph.append(node)

    graph.sort(key=lambda item: str(item.get("@id", "")))
    payload: dict[str, Any] = {
        "@context": deepcopy(jsonld_context),
        "@graph": graph,
    }
    if assembled.excluded_entities:
        excluded = deepcopy(assembled.excluded_entities)
        # `excluded_entities` carry the same `_`-prefixed internal fields
        # as `@graph` nodes (nested under each record's `entity`). Honour
        # the flag here too, so `include_internal_fields=False` yields a
        # response with zero `_` fields anywhere — not just in `@graph`.
        if not include_internal_fields:
            for record in excluded:
                inner = record.get("entity") if isinstance(record, dict) else None
                if isinstance(inner, dict):
                    record["entity"] = _drop_internal_keys(inner)
        payload["excluded_entities"] = excluded
    return payload
