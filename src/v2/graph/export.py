from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rdflib import Graph, URIRef

if TYPE_CHECKING:
    from src.v2.graph.store import GraphStore

ENTITY_URI_PREFIX = "urn:pulse:"
CONTEXT_RELATIVE_PATH = Path("src/v2/schema/json/context/v2.0.jsonld")

_ENTITY_TYPE_ALIASES: dict[str, set[str]] = {
    "person": {"person", "schema:person", "pulse:person"},
    "repository": {"repository", "schema:softwaresourcecode", "pulse:repository"},
    "organization": {"organization", "org:organization", "pulse:organization"},
    "membership": {"membership", "org:membership", "pulse:membership"},
    "contribution": {"contribution", "pulse:contribution"},
    "article": {"article", "schema:scholarlyarticle", "pulse:article"},
}


def _normalize_entity_type(value: str) -> str:
    normalized = value.strip().lower()
    for canonical, aliases in _ENTITY_TYPE_ALIASES.items():
        if normalized in aliases:
            return canonical
    return normalized


def _to_entity_uri(entity_id: str) -> URIRef:
    if entity_id.startswith(("http://", "https://", "urn:")):
        return URIRef(entity_id)
    return URIRef(f"{ENTITY_URI_PREFIX}{entity_id}")


def _is_entity_uri(node_value: Any) -> bool:
    return isinstance(node_value, URIRef) and str(node_value).startswith(ENTITY_URI_PREFIX)


def _parse_jsonld_graph(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        raw_graph = payload.get("@graph")
        if isinstance(raw_graph, list):
            return [item for item in raw_graph if isinstance(item, dict)]
        return [payload]
    return []


def _node_types(node: dict[str, Any]) -> list[str]:
    raw_type = node.get("@type")
    if isinstance(raw_type, str):
        return [raw_type]
    if isinstance(raw_type, list):
        return [item for item in raw_type if isinstance(item, str)]
    return []


def _prune_dangling_entity_refs(
    value: Any,
    *,
    included_ids: set[str],
    all_entity_ids: set[str],
) -> Any:
    if isinstance(value, dict):
        ref_id = value.get("@id")
        if isinstance(ref_id, str):
            if ref_id in all_entity_ids and ref_id not in included_ids:
                return None
            return {"@id": ref_id}
        return value

    if isinstance(value, list):
        pruned_items = [
            item
            for item in (
                _prune_dangling_entity_refs(
                    item,
                    included_ids=included_ids,
                    all_entity_ids=all_entity_ids,
                )
                for item in value
            )
            if item is not None
        ]
        return pruned_items if pruned_items else None

    return value


class JSONLDExporter:
    def __init__(self, *, context_path: Path | None = None) -> None:
        default_path = Path(__file__).resolve().parents[3] / CONTEXT_RELATIVE_PATH
        self._context_path = context_path if context_path is not None else default_path

    def get_context(self) -> dict[str, Any]:
        return _load_context_file(self._context_path)

    def export_full_graph(self, graph: Graph) -> dict[str, Any]:
        context_payload = self.get_context()
        raw_json = graph.serialize(
            format="json-ld",
            context=context_payload["@context"],
            auto_compact=True,
        )
        graph_payload = json.loads(raw_json)
        graph_nodes = _parse_jsonld_graph(graph_payload)
        graph_nodes.sort(key=lambda node: str(node.get("@id", "")))
        return {
            "@context": context_payload["@context"],
            "@graph": graph_nodes,
        }

    def export_entity(self, entity_id: str, graph: Graph) -> dict[str, Any]:
        subject = _to_entity_uri(entity_id)
        filtered_graph = Graph()

        for triple in graph.triples((subject, None, None)):
            _, _, object_value = triple
            if _is_entity_uri(object_value) and object_value != subject:
                continue
            filtered_graph.add(triple)

        return self.export_full_graph(filtered_graph)

    def export_filtered(  # noqa: C901, PLR0912
        self,
        graph: Graph,
        *,
        source_url: str | None = None,
        entity_types: list[str] | None = None,
        store: GraphStore | None = None,
    ) -> dict[str, Any]:
        exported = self.export_full_graph(graph)
        nodes = [item for item in exported["@graph"] if isinstance(item, dict)]
        if not nodes:
            return exported

        requested_types = {
            _normalize_entity_type(entity_type)
            for entity_type in (entity_types or [])
            if entity_type.strip()
        }

        source_entity_ids: set[str] | None = None
        if source_url is not None:
            if store is None:
                source_entity_ids = set()
            else:
                source_entity_ids = store.get_entity_ids_by_run(source_url)

        all_entity_ids = {
            str(node["@id"])
            for node in nodes
            if isinstance(node.get("@id"), str)
        }
        included_ids: set[str] = set()
        source_entity_uris = {_to_entity_uri(entity_id) for entity_id in source_entity_ids or set()}
        source_entity_uri_strings = {str(entity_uri) for entity_uri in source_entity_uris}

        for node in nodes:
            node_id = node.get("@id")
            if not isinstance(node_id, str):
                continue

            if source_entity_ids is not None and node_id not in source_entity_uri_strings:
                continue

            if requested_types:
                normalized_types = {
                    _normalize_entity_type(type_value)
                    for type_value in _node_types(node)
                }
                if not (normalized_types & requested_types):
                    continue

            included_ids.add(node_id)

        filtered_nodes: list[dict[str, Any]] = []
        for node in nodes:
            node_id = node.get("@id")
            if not isinstance(node_id, str) or node_id not in included_ids:
                continue

            filtered_node: dict[str, Any] = {}
            for key, value in node.items():
                pruned_value = _prune_dangling_entity_refs(
                    value,
                    included_ids=included_ids,
                    all_entity_ids=all_entity_ids,
                )
                if pruned_value is None and key not in {"@id", "@type"}:
                    continue
                filtered_node[key] = pruned_value
            filtered_nodes.append(filtered_node)

        filtered_nodes.sort(key=lambda item: str(item.get("@id", "")))
        return {
            "@context": exported["@context"],
            "@graph": filtered_nodes,
        }


@lru_cache(maxsize=8)
def _load_context_file(context_path: Path) -> dict[str, Any]:
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        message = f"Context file {context_path} must contain a JSON object"
        raise TypeError(message)
    if "@context" not in payload:
        message = f"Context file {context_path} must define '@context'"
        raise ValueError(message)
    return payload
