"""Build a per-entity-type vocabulary cheatsheet from the v2 SHACL shapes graph.

The terminal-agent failures observed in run 6 were dominated by closed-shape
SHACL violations: the agent emitted `schema:legalName`, `schema:description`,
`schema:logo` (etc.) on Organizations because those are valid `schema.org`
properties — but the v2 ontology shapes are CLOSED, so those properties are
violations. Without seeing the allowed vocabulary, the agent has no way to
guess the right subset.

This module extracts the allowed (and required) properties per shape from
the live shapes graph, formats them as a compact markdown cheatsheet, and
hands it to the runner so it lands in `<workdir>/schema_cheatsheet.md`
alongside `gimie.jsonld`. The MISSION instructs the agent to read it
before adding any entity.

Pulled live from `load_ontology_shapes_graph()` so the cheatsheet stays in
sync with whatever shapes the same SHACL validator will run against.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

# JSON-LD prefix bindings the agent uses in `output.jsonld` — must match
# the v2 @context. Used to compact the long IRIs that come back from
# the shapes graph into the CURIE form the agent reads/writes.
_PREFIX_BINDINGS: list[tuple[str, str]] = [
    ("schema:", "http://schema.org/"),
    ("org:", "http://www.w3.org/ns/org#"),
    ("pulse:", "https://open-pulse.epfl.ch/ontology#"),
    ("xsd:", "http://www.w3.org/2001/XMLSchema#"),
]


def _compact(iri: Any) -> str:
    """Compact an rdflib URI to its v2 CURIE form when possible."""
    if iri is None:
        return ""
    text = str(iri)
    for prefix, expansion in _PREFIX_BINDINGS:
        if text.startswith(expansion):
            return prefix + text[len(expansion) :]
    return text


_SHAPES_QUERY = """
PREFIX sh: <http://www.w3.org/ns/shacl#>
SELECT ?shape ?targetClass ?closed ?path ?minCount ?datatype ?cls
WHERE {
  ?shape a sh:NodeShape ;
         sh:targetClass ?targetClass .
  OPTIONAL { ?shape sh:closed ?closed }
  OPTIONAL {
    ?shape sh:property ?prop .
    ?prop  sh:path     ?path .
    OPTIONAL { ?prop sh:minCount ?minCount }
    OPTIONAL { ?prop sh:datatype ?datatype }
    OPTIONAL { ?prop sh:class ?cls }
  }
}
ORDER BY ?targetClass ?path
"""


def build_cheatsheet() -> str:
    """Render a markdown cheatsheet of allowed/required properties per shape."""
    shapes = _query_shapes()
    if not shapes:
        return _STUB_CHEATSHEET
    return _render(shapes)


def get_shape_constraints() -> dict[str, dict[str, Any]]:
    """Return per-`@type` constraints suitable for a deterministic scrubber.

    Shape of return value::

        {
          "<curie_type>": {
              "allowed":  set[str]   # all curie property paths in shape
              "required": set[str]   # subset with sh:minCount >= 1
              "datetime_props": set[str]  # subset with sh:datatype xsd:dateTime
              "closed": bool
          },
          ...
        }

    Built from the same SPARQL query as `build_cheatsheet()` so the
    markdown the agent reads and the scrubber's behaviour can never
    drift apart.
    """
    shapes = _query_shapes()
    out: dict[str, dict[str, Any]] = {}
    for shape in shapes.values():
        target: str = shape["target"]
        allowed: set[str] = set()
        required: set[str] = set()
        datetime_props: set[str] = set()
        for path, meta in shape["properties"].items():
            allowed.add(path)
            if meta.get("required"):
                required.add(path)
            if meta.get("value_type") == "xsd:dateTime":
                datetime_props.add(path)
        out[target] = {
            "allowed": allowed,
            "required": required,
            "datetime_props": datetime_props,
            "closed": shape.get("closed", False),
        }
    return out


def _query_shapes() -> OrderedDict[str, dict[str, Any]]:
    """Run the SPARQL query, return shapes keyed by IRI."""
    try:
        from src.v2.validation.ontology import load_ontology_shapes_graph  # noqa: PLC0415
    except ImportError as err:
        logger.warning("schema_cheatsheet: ontology unavailable (%s)", err)
        return OrderedDict()

    try:
        graph = load_ontology_shapes_graph()
    except Exception as err:  # noqa: BLE001 — soft failure, caller falls back
        logger.warning("schema_cheatsheet: load_ontology_shapes_graph failed (%s)", err)
        return OrderedDict()

    # shape_iri → {target, closed, properties: ordered dict path → meta}
    shapes: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in graph.query(_SHAPES_QUERY):
        shape_iri = str(row.shape)
        info = shapes.setdefault(
            shape_iri,
            {
                "target": _compact(row.targetClass),
                "closed": bool(row.closed) if row.closed is not None else False,
                "properties": OrderedDict(),
            },
        )
        if row.path is None:
            continue  # shape has no properties (rare but valid)
        path_curie = _compact(row.path)
        prop = info["properties"].setdefault(
            path_curie,
            {"required": False, "value_type": None},
        )
        if row.minCount is not None and int(row.minCount) >= 1:
            prop["required"] = True
        if row.cls is not None:
            prop["value_type"] = f"@id of {_compact(row.cls)}"
        elif row.datatype is not None:
            prop["value_type"] = _compact(row.datatype)

    return shapes


def _render(shapes: OrderedDict[str, dict[str, Any]]) -> str:
    if not shapes:
        return _STUB_CHEATSHEET

    lines: list[str] = [
        "# v2 Open Pulse Ontology — vocabulary cheatsheet",
        "",
        (
            "These are the **only** properties the v2 SHACL shapes accept on "
            "each entity type. Every shape is `sh:closed = true`: any other "
            "property — even a valid `schema.org` one — fails validation."
        ),
        "",
        (
            "Use the CURIE form shown here verbatim in `output.jsonld` "
            "(matches the `@context` you have)."
        ),
        "",
        "## Required `identifiers.uuid`",
        "",
        (
            "Every entity also needs `identifiers: { uuid: \"<placeholder>\" }`. "
            "Use the placeholder format `00000000-0000-0000-0000-00000000000N` "
            "with sequential `N` — the orchestrator replaces these with real "
            "UUIDv4s after your run."
        ),
        "",
    ]
    # Stable display order: Repository, Person, Organization first (PoC scope),
    # then anything else that exists.
    preferred_order = [
        "schema:SoftwareSourceCode",
        "schema:Person",
        "org:Organization",
        "schema:ScholarlyArticle",
        "org:Membership",
        "pulse:Contribution",
    ]
    sorted_shapes = sorted(
        shapes.values(),
        key=lambda s: (
            preferred_order.index(s["target"])
            if s["target"] in preferred_order
            else 99
        ),
    )
    for shape in sorted_shapes:
        target = shape["target"]
        closed_marker = " *(closed shape)*" if shape["closed"] else ""
        lines.append(f"## `@type`: `{target}`{closed_marker}")
        lines.append("")
        if not shape["properties"]:
            lines.append("(no constrained properties)")
            lines.append("")
            continue
        lines.append("| property | required | value type |")
        lines.append("|---|---|---|")
        for path, meta in shape["properties"].items():
            req = "yes" if meta["required"] else "no"
            value_type = meta["value_type"] or "any"
            lines.append(f"| `{path}` | {req} | {value_type} |")
        lines.append("")
    lines.append("## Forbidden patterns (common pitfalls)")
    lines.append("")
    lines.append(
        "- Do NOT emit `schema:legalName`, `schema:description`, `schema:logo`, "
        "`schema:url`, `schema:image` on `org:Organization` — they are not in "
        "the closed shape. If you have only a name, emit just `schema:name`."
    )
    lines.append(
        "- Do NOT emit `schema:affiliation` on `schema:Person` — the v2 "
        "model represents affiliations through dedicated `org:Membership` "
        "entities (linked via `org:hasMembership` from the Person and "
        "`org:organization` from the Membership). Build a Membership entity "
        "instead of inlining."
    )
    lines.append(
        "- Do NOT emit free-form `schema:description`, `schema:keywords` on "
        "`schema:SoftwareSourceCode` — same closed-shape rule. Stick to the "
        "table above."
    )
    lines.append("")
    return "\n".join(lines)


_STUB_CHEATSHEET = (
    "# v2 vocabulary cheatsheet (unavailable)\n\n"
    "Could not load the SHACL shapes graph. Use only properties you can "
    "verify in the v2 ontology — when in doubt, omit a property rather "
    "than guess.\n"
)


__all__ = ["build_cheatsheet", "get_shape_constraints"]
