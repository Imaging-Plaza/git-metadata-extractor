from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD
from rdflib.term import Node

from src.v2.graph.models import Edge

if TYPE_CHECKING:
    from src.v2.graph.store import GraphStore

Triple = tuple[URIRef, URIRef, Node]

PULSE_NAMESPACE = Namespace("https://open-pulse.epfl.ch/ontology#")
SCHEMA_NAMESPACE = Namespace("http://schema.org/")
ORG_NAMESPACE = Namespace("http://www.w3.org/ns/org#")
TIME_NAMESPACE = Namespace("http://www.w3.org/2006/time#")
DCT_NAMESPACE = Namespace("http://purl.org/dc/terms/")
RDFS_NAMESPACE = Namespace("http://www.w3.org/2000/01/rdf-schema#")
OWL_NAMESPACE = Namespace("http://www.w3.org/2002/07/owl#")
SKOS_NAMESPACE = Namespace("http://www.w3.org/2004/02/skos/core#")
SH_NAMESPACE = Namespace("http://www.w3.org/ns/shacl#")
WD_NAMESPACE = Namespace("http://www.wikidata.org/entity/")
ENTITY_NAMESPACE = Namespace("urn:git-metadata-extractor:entity:")
NAMESPACE_BY_PREFIX: dict[str, Any] = {
    "pulse": PULSE_NAMESPACE,
    "schema": SCHEMA_NAMESPACE,
    "org": ORG_NAMESPACE,
    "time": TIME_NAMESPACE,
    "dct": DCT_NAMESPACE,
    "rdfs": RDFS_NAMESPACE,
    "rdf": RDF,
    "owl": OWL_NAMESPACE,
    "skos": SKOS_NAMESPACE,
    "sh": SH_NAMESPACE,
    "wd": WD_NAMESPACE,
    "xsd": XSD,
}
LITERAL_STRING_PREDICATES: frozenset[str] = frozenset(
    {
        str(SCHEMA_NAMESPACE["identifier"]),
    },
)
ENTITY_TYPE_CLASS_MAP: dict[str, str] = {
    "person": "pulse:Person",
    "repository": "pulse:Repository",
    "organization": "pulse:Organization",
    "membership": "pulse:Membership",
    "contribution": "pulse:Contribution",
    "article": "pulse:Article",
}


class RDFGraphSync:
    def load_from_store(self, store: GraphStore) -> Graph:
        graph = Graph()
        graph.bind("pulse", PULSE_NAMESPACE)
        graph.bind("schema", SCHEMA_NAMESPACE)

        for entity in store.get_all_entities():
            entity_payload = {
                "id": entity.id,
                "type": entity.type,
                **entity.data,
                "identifiers": entity.identifiers,
            }
            self.apply_entity_delta(
                graph=graph,
                entity_type=entity.type,
                entity_data=entity_payload,
                action="upsert",
            )

        for edge in store.get_all_edges():
            self.apply_edge_delta(graph=graph, edge=edge, action="upsert")

        return graph

    def apply_entity_delta(
        self,
        graph: Graph,
        entity_type: str,
        entity_data: dict[str, Any],
        action: str = "upsert",
    ) -> None:
        subject = _entity_uri(str(entity_data["id"]))
        if action == "delete":
            graph.remove((subject, None, None))
            graph.remove((None, None, subject))
            return
        if action != "upsert":
            message = f"Unsupported entity delta action: {action}"
            raise ValueError(message)

        graph.remove((subject, None, None))
        for triple in self.entity_to_triples(entity_type, entity_data):
            graph.add(triple)

    def apply_edge_delta(
        self,
        graph: Graph,
        edge: Edge | dict[str, Any],
        action: str = "upsert",
    ) -> None:
        relation_type, source_id, target_id = _extract_edge_payload(edge)
        triple = (
            _entity_uri(source_id),
            _predicate_uri(relation_type),
            _entity_uri(target_id),
        )
        if action == "delete":
            graph.remove(triple)
            return
        if action != "upsert":
            message = f"Unsupported edge delta action: {action}"
            raise ValueError(message)

        graph.remove(triple)
        graph.add(triple)

    def entity_to_triples(
        self,
        entity_type: str,
        entity_data: dict[str, Any],
    ) -> list[Triple]:
        if "id" not in entity_data:
            message = "entity_data must include an 'id' key"
            raise ValueError(message)

        subject = _entity_uri(str(entity_data["id"]))
        type_value = _normalize_entity_type(
            entity_data.get("type", entity_type),
        )
        triples: list[Triple] = [
            (subject, RDF.type, _coerce_uri_or_literal(type_value, for_type=True)),
        ]
        for key, value in entity_data.items():
            if key in {"id", "type"} or value is None:
                continue
            predicate = _predicate_uri(key)
            if isinstance(value, list):
                for item in value:
                    if item is None:
                        continue
                    triples.append(
                        (
                            subject,
                            predicate,
                            _coerce_uri_or_literal(item, predicate=predicate),
                        ),
                    )
                continue
            triples.append(
                (
                    subject,
                    predicate,
                    _coerce_uri_or_literal(value, predicate=predicate),
                ),
            )

        return triples


def _extract_edge_payload(edge: Edge | dict[str, Any]) -> tuple[str, str, str]:
    if isinstance(edge, Edge):
        return edge.relation_type, edge.source_id, edge.target_id
    if isinstance(edge, dict):
        return (
            str(edge["relation_type"]),
            str(edge["source_id"]),
            str(edge["target_id"]),
        )
    message = "edge must be an Edge or dict payload"
    raise TypeError(message)


def _entity_uri(entity_id: str) -> URIRef:
    if _looks_like_uri(entity_id):
        return URIRef(entity_id)
    return URIRef(ENTITY_NAMESPACE[entity_id])


def _predicate_uri(raw_key: str) -> URIRef:
    resolved = _uri_from_text(raw_key)
    if resolved is not None:
        return resolved
    return URIRef(PULSE_NAMESPACE[raw_key])


def _coerce_uri_or_literal(  # noqa: PLR0911
    value: Any,
    *,
    for_type: bool = False,
    predicate: URIRef | None = None,
) -> Node:
    if predicate is not None and str(predicate) in LITERAL_STRING_PREDICATES:
        return Literal(str(value), datatype=XSD.string)

    primitive_literal = _primitive_literal(value)
    if primitive_literal is not None:
        return primitive_literal
    if isinstance(value, dict):
        return Literal(json.dumps(value, sort_keys=True))
    if isinstance(value, list):
        return Literal(json.dumps(value, sort_keys=True))

    text = str(value)
    resolved_uri = _uri_from_text(text)
    if resolved_uri is not None:
        return resolved_uri

    if for_type:
        return URIRef(PULSE_NAMESPACE[text])
    return Literal(text)


def _primitive_literal(value: Any) -> Literal | None:
    if value is None:
        return Literal("")
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, int):
        return Literal(value, datatype=XSD.integer)
    if isinstance(value, float):
        return Literal(value, datatype=XSD.double)
    return None


def _uri_from_text(value: str) -> URIRef | None:
    if _looks_like_uri(value):
        return URIRef(value)
    if ":" not in value:
        return None
    prefix, suffix = value.split(":", 1)
    namespace = NAMESPACE_BY_PREFIX.get(prefix)
    if namespace is None or not suffix:
        return None
    return URIRef(namespace[suffix])


def _looks_like_uri(value: str) -> bool:
    return value.startswith(("http://", "https://", "urn:"))


def _normalize_entity_type(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    normalized = value.strip()
    if not normalized:
        return value
    if _looks_like_uri(normalized) or ":" in normalized:
        return normalized

    return ENTITY_TYPE_CLASS_MAP.get(normalized.lower(), normalized)
