from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.v2.pipeline.stages.models import AssembledOutput

ENTITY_URI_PREFIX = "urn:pulse:"
HELPER_ONLY_FIELDS = {"shacl", "identifiers", "idSource", "_person_ref"}

# Auxiliary namespace for the rich provider metadata the extractor
# collects but the Open Pulse ontology does not (yet) model. Surfaced
# only when `include_internal_fields=True`: a `_avatar_url` field is
# emitted as the JSON-LD term `gme-internal:avatar_url`, which expands
# to a real IRI so the payload loads into an RDF triplestore. This is a
# separate vocabulary — it does NOT touch the Open Pulse ontology, and
# such output is intentionally not conformant to its closed SHACL
# shapes (a triplestore load needs no SHACL conformance).
GME_INTERNAL_PREFIX = "gme-internal"
GME_INTERNAL_NAMESPACE = "https://openpulse.science/git-metadata-extractor#"

# Auxiliary namespace for parsed publiccode.yml metadata. Same strategy
# as `gme-internal:` (separate vocabulary, surfaced only when
# `include_internal_fields=True`, does NOT touch the Open Pulse
# ontology) — but with its own prefix so downstream RDF consumers can
# recognise publiccode triples as such. The base IRI points at the
# publiccode standard's docs URL (no formal ontology IRI exists for
# the spec, but the docs URL is stable and identifies the field defs).
#
# Repository entities whose `_publiccode` field is populated have its
# top-level scalar/list fields hoisted to `publiccode:<field>`
# predicates so SPARQL queries like `?repo publiccode:license ?l` work
# without re-parsing JSON. Nested sub-trees (legal / maintenance /
# localisation / description / dependsOn / intendedAudience) remain
# accessible via the `gme-internal:publiccode` JSON blob so structured
# consumers don't lose data.
PUBLICCODE_PREFIX = "publiccode"
PUBLICCODE_NAMESPACE = "https://yml.publiccode.tools/"

# Top-level publiccode v0.4 fields that are scalars or lists of
# scalars — safe to hoist to `publiccode:<field>` triples. Anything not
# in this set stays as nested JSON under `gme-internal:publiccode`.
_PUBLICCODE_FLAT_FIELDS: frozenset[str] = frozenset({
    # Scalars
    "publiccodeYmlVersion", "name", "url", "applicationSuite",
    "landingURL", "softwareVersion", "releaseDate", "logo",
    "monochromeLogo", "roadmap", "developmentStatus", "softwareType",
    # List-of-strings
    "isBasedOn", "platforms", "categories", "usedBy",
})


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


def _internal_term(key: str) -> str:
    """Map a `_`-prefixed internal field to its `gme-internal:` JSON-LD
    term (`_avatar_url` -> `gme-internal:avatar_url`)."""
    return f"{GME_INTERNAL_PREFIX}:{key.lstrip('_')}"


def _publiccode_term(field: str) -> str:
    """`license` -> `publiccode:license`."""
    return f"{PUBLICCODE_PREFIX}:{field}"


def _hoist_publiccode_scalars(
    publiccode: Any,
) -> dict[str, Any]:
    """Pull the scalar/list top-level fields from a parsed publiccode
    payload into a flat ``{publiccode:<field>: value}`` dict for direct
    emission as RDF predicates. Returns ``{}`` when the payload is
    None / wrong-shape / missing every flat field.

    Nested sub-trees (legal, maintenance, …) are intentionally NOT
    hoisted here — they need their own predicate mapping per leaf,
    which gets ambiguous fast (description has per-language sub-trees,
    contractors/contacts are lists of dicts). They remain reachable
    via `gme-internal:publiccode`."""
    if not isinstance(publiccode, dict):
        return {}
    out: dict[str, Any] = {}
    for field in _PUBLICCODE_FLAT_FIELDS:
        value = publiccode.get(field)
        if value is None:
            continue
        if isinstance(value, str) and not value:
            continue
        if isinstance(value, list) and not value:
            continue
        out[_publiccode_term(field)] = value
    return out


def _rewrite_internal_keys(entity: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``entity`` with top-level `_`-prefixed keys
    renamed to their `gme-internal:` terms, so they expand to real IRI
    predicates instead of being dropped as undefined JSON-LD terms."""
    out: dict[str, Any] = {}
    for key, value in entity.items():
        if isinstance(key, str) and key.startswith("_"):
            out[_internal_term(key)] = value
        else:
            out[key] = value
    return out


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

    Set ``include_internal_fields=True`` to surface the broader profile
    metadata (`_avatar_url`, `_bio`, `_company`, `_orcid_keywords`,
    `_dropped_affiliations`, etc.) that we collect but don't yet have
    ontology terms for. Each `_`-prefixed key is renamed to a
    `gme-internal:` JSON-LD term (`_avatar_url` -> `gme-internal:avatar_url`)
    and the `gme-internal` prefix is registered in `@context`, so the
    payload expands to real IRI triples and loads into an RDF
    triplestore. This applies to `@graph` nodes and `excluded_entities`
    alike. Strict SHACL validation has already run by this point (it
    always strips `_` fields), so flipping this flag is purely about
    what the consumer sees, not about validation — and the resulting
    document is intentionally not conformant to the closed Open Pulse
    SHACL shapes.
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
            out_key = key
            if isinstance(key, str) and key.startswith("_"):
                if not include_internal_fields:
                    continue
                # Emit as a `gme-internal:` term so it expands to a real
                # IRI predicate rather than a dropped undefined term.
                out_key = _internal_term(key)
            node[out_key] = _normalize_jsonld_value(
                value,
                id_map=id_map,
                iri_typed_terms=iri_typed_terms,
                current_property=out_key,
            )

        # Hoist scalar/list top-level publiccode fields to `publiccode:`
        # predicates so SPARQL queries can hit them directly. The
        # `gme-internal:publiccode` JSON blob above still carries the
        # full nested payload for consumers that need the sub-trees.
        if include_internal_fields:
            for pc_term, pc_value in _hoist_publiccode_scalars(
                entity.get("_publiccode"),
            ).items():
                node[pc_term] = _normalize_jsonld_value(
                    pc_value,
                    id_map=id_map,
                    iri_typed_terms=iri_typed_terms,
                    current_property=pc_term,
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
    context = deepcopy(jsonld_context)
    if include_internal_fields:
        # Register the prefixes so `gme-internal:*` and `publiccode:*`
        # terms expand to IRIs (and the payload loads into RDF stores).
        context[GME_INTERNAL_PREFIX] = GME_INTERNAL_NAMESPACE
        context[PUBLICCODE_PREFIX] = PUBLICCODE_NAMESPACE
    payload: dict[str, Any] = {
        "@context": context,
        "@graph": graph,
    }
    if assembled.excluded_entities:
        excluded = deepcopy(assembled.excluded_entities)
        # `excluded_entities` carry the same `_`-prefixed internal fields
        # as `@graph` nodes (nested under each record's `entity`). Honour
        # the flag here too: drop them when off, rename them to
        # `gme-internal:` terms when on — so the whole response is
        # consistent (zero `_` fields, or all of them as real IRIs).
        for record in excluded:
            inner = record.get("entity") if isinstance(record, dict) else None
            if isinstance(inner, dict):
                record["entity"] = (
                    _rewrite_internal_keys(inner)
                    if include_internal_fields
                    else _drop_internal_keys(inner)
                )
        payload["excluded_entities"] = excluded
    return payload
