# ruff: noqa: INP001
"""Read SHACL node shapes into a plain intermediate representation.

This is the half of ontology-driven codegen worth testing on its own: getting
constraints *out* of the shapes correctly is where the errors live, and it is
much easier to assert against a dict than against emitted source.

The reader deliberately understands only what the Open Pulse shapes actually
use. Two forms of property shape appear, and both must work:

    sh:property pulse:NameShape ;                          # named, reusable
    sh:property [ sh:path pulse:owns ; sh:class ... ] ;    # inline blank node

`sh:or` blocks are read as *identity alternatives* rather than as a general
disjunction, because that is the only way the shapes use them: every one lists
the identifier properties of which at least one must be present (ORCID or a
profile, ROR or a profile, and so on). Treating them generally would produce
unusable models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS, Namespace

SH = Namespace("http://www.w3.org/ns/shacl#")
PULSE = Namespace("https://open-pulse.epfl.ch/ontology#")
SCHEMA = Namespace("http://schema.org/")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")

#: Every prefix the ontology files declare, longest-base-first where one base
#: could prefix another. One table, because two consumers need it: `_curie`
#: compacts terms with it, and the JSON-LD context emitter writes it out
#: verbatim as the context's prefix block. Keeping a second copy in the emitter
#: is how a context ends up unable to expand the terms in its own models.
#:
#: `wd:` matters as much as `pulse:`: every one of the 1606 discipline
#: enumeration members is a Wikidata entity, and without it they compact to
#: nothing and the generated member set matches none of the `wd:Q...` values
#: the pipeline actually emits.
PREFIXES: tuple[tuple[str, str], ...] = (
    ("pulse", "https://open-pulse.epfl.ch/ontology#"),
    ("sh", "http://www.w3.org/ns/shacl#"),
    ("schema", "http://schema.org/"),
    ("org", "http://www.w3.org/ns/org#"),
    ("prov", "http://www.w3.org/ns/prov#"),
    ("time", "http://www.w3.org/2006/time#"),
    ("xsd", "http://www.w3.org/2001/XMLSchema#"),
    ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
    ("owl", "http://www.w3.org/2002/07/owl#"),
    ("wd", "http://www.wikidata.org/entity/"),
    ("rdfs", "http://www.w3.org/2000/01/rdf-schema#"),
    ("skos", "http://www.w3.org/2004/02/skos/core#"),
    ("dct", "http://purl.org/dc/terms/"),
    ("coar-access", "http://purl.org/coar/access_right/"),
    ("coar-resource", "http://purl.org/coar/resource_type/"),
    ("datacite", "http://purl.org/spar/datacite/"),
)


@dataclass(slots=True)
class PropertyConstraint:
    """One `sh:property` on a node shape, flattened."""

    path: str
    name: str | None = None
    description: str | None = None
    datatype: str | None = None
    node_class: str | None = None
    node_kind: str | None = None
    min_count: int | None = None
    max_count: int | None = None
    pattern: str | None = None
    #: Classes from a *property-level* `sh:or`, e.g. `pulse:ownedBy`, whose
    #: value may be a Person or an Organization. Distinct from the node-level
    #: `sh:or` in `NodeShape.identity_alternatives`: that one says which
    #: properties identify a node, this one says which types a value may have.
    class_alternatives: list[str] = field(default_factory=list)

    @property
    def required(self) -> bool:
        return (self.min_count or 0) >= 1

    @property
    def single_valued(self) -> bool:
        return self.max_count == 1

    @property
    def is_reference(self) -> bool:
        """True when the value is another node rather than a literal.

        `class_alternatives` has to count. Four properties carry their type
        only in a property-level `sh:or` — `pulse:ownedBy` among them — and
        without this they read as untyped, which in a JSON-LD context means the
        term loses `@type: "@id"` and the value expands as a plain literal
        instead of a reference. The edge silently stops being an edge.
        """
        return (
            self.node_class is not None
            or self.node_kind == "IRI"
            or bool(self.class_alternatives)
        )


@dataclass(slots=True)
class NodeShape:
    """One `sh:NodeShape` with a `sh:targetClass`."""

    shape_iri: str
    target_class: str
    closed: bool = False
    ignored_properties: list[str] = field(default_factory=list)
    properties: list[PropertyConstraint] = field(default_factory=list)
    #: Each entry is one alternative from an `sh:or`: the property paths that
    #: branch requires. `[["pulse:orcidIdentifier"], ["pulse:hasProfile"]]`
    #: means "ORCID or at least one profile".
    identity_alternatives: list[list[str]] = field(default_factory=list)

    @property
    def local_name(self) -> str:
        return self.shape_iri.rsplit("#", maxsplit=1)[-1].rsplit("/", maxsplit=1)[-1]


def _curie(term: Any) -> str | None:
    """Compact a URIRef the way the ontology writes it, e.g. `pulse:owns`."""
    if not isinstance(term, URIRef):
        return None
    text = str(term)
    for prefix, base in PREFIXES:
        if text.startswith(base):
            return f"{prefix}:{text[len(base):]}"
    return text


def _literal(value: Any) -> Any:
    return value.toPython() if hasattr(value, "toPython") else value


def _property_class_alternatives(graph: Graph, node: Any) -> list[str]:
    """Classes offered by a property-level `sh:or ( [sh:class A] [sh:class B] )`."""
    alternatives: list[str] = []
    for or_list in graph.objects(node, SH["or"]):
        for member in graph.items(or_list):
            member_class = _curie(graph.value(member, SH["class"]))
            if member_class:
                alternatives.append(member_class)
    return alternatives


def _read_property(graph: Graph, node: Any) -> PropertyConstraint | None:
    path = _curie(graph.value(node, SH.path))
    if path is None:
        return None
    max_count = graph.value(node, SH.maxCount)
    min_count = graph.value(node, SH.minCount)
    node_kind = _curie(graph.value(node, SH.nodeKind))
    return PropertyConstraint(
        path=path,
        name=_literal(graph.value(node, SH.name)),
        description=_literal(graph.value(node, SH.description)),
        datatype=_curie(graph.value(node, SH.datatype)),
        node_class=_curie(graph.value(node, SH["class"])),
        node_kind=node_kind.removeprefix("sh:") if node_kind else None,
        min_count=int(_literal(min_count)) if min_count is not None else None,
        max_count=int(_literal(max_count)) if max_count is not None else None,
        pattern=_literal(graph.value(node, SH.pattern)),
        class_alternatives=_property_class_alternatives(graph, node),
    )


def _read_identity_alternatives(graph: Graph, shape: Any) -> list[list[str]]:
    """Flatten `sh:or ( [ sh:property [ sh:path X ; sh:minCount 1 ] ] ... )`."""
    alternatives: list[list[str]] = []
    for or_list in graph.objects(shape, SH["or"]):
        for member in graph.items(or_list):
            paths: list[str] = []
            for prop in graph.objects(member, SH.property):
                path = _curie(graph.value(prop, SH.path))
                if path:
                    paths.append(path)
            # Some branches put sh:path directly on the member.
            direct = _curie(graph.value(member, SH.path))
            if direct:
                paths.append(direct)
            if paths:
                alternatives.append(paths)
    return alternatives


def read_version(path: Path) -> str | None:
    """The `owl:versionInfo` on the file's `owl:Ontology` header.

    Preferred over the submodule SHA for stamping generated artefacts: it is
    what the ontology says about itself, so it survives a re-tag or a mirror
    and does not make the drift gate depend on git state.
    """
    graph = Graph()
    graph.parse(str(path), format="turtle")
    for subject in graph.subjects(RDF.type, OWL.Ontology):
        version = graph.value(subject, OWL.versionInfo)
        if version is not None:
            return str(_literal(version))
    return None


def read_shapes(path: Path) -> list[NodeShape]:
    """Every `sh:NodeShape` in `path` that declares a `sh:targetClass`.

    Property shapes without a target class are reusable fragments referenced by
    node shapes; they are followed rather than returned.
    """
    graph = Graph()
    graph.parse(str(path), format="turtle")

    shapes: list[NodeShape] = []
    for subject in graph.subjects(RDF.type, SH.NodeShape):
        target = _curie(graph.value(subject, SH.targetClass))
        if target is None:
            continue
        closed = bool(_literal(graph.value(subject, SH.closed)) or False)
        ignored = [
            _curie(term) or ""
            for lst in graph.objects(subject, SH.ignoredProperties)
            for term in graph.items(lst)
        ]
        properties: list[PropertyConstraint] = []
        for prop in graph.objects(subject, SH.property):
            constraint = _read_property(graph, prop)
            if constraint is not None:
                properties.append(constraint)
        shapes.append(
            NodeShape(
                shape_iri=str(subject),
                target_class=target,
                closed=closed,
                ignored_properties=[i for i in ignored if i],
                properties=sorted(properties, key=lambda c: c.path),
                identity_alternatives=_read_identity_alternatives(graph, subject),
            ),
        )
    return sorted(shapes, key=lambda s: s.local_name)


@dataclass(slots=True)
class Enumeration:
    """One `rdfs:subClassOf schema:Enumeration` class and its instances."""

    curie: str
    members: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    definition: str | None = None

    @property
    def local_name(self) -> str:
        return self.curie.split(":", maxsplit=1)[-1]

    @property
    def alias_name(self) -> str:
        """`pulse:PlatformEnumeration` -> `Platform`, the generated type name."""
        return self.local_name.removesuffix("Enumeration")


def read_enumerations(paths: list[Path]) -> list[Enumeration]:
    """Read every enumeration class from *paths* as a single merged graph.

    Merging is not an optimisation, it is required for correctness.
    `pulse:PublicationTypeEnumeration` is declared in the canonical file with
    seven members and *extended* by the raw file with eight more (the Zenodo
    upload types). Reading either file alone yields a confidently wrong
    enumeration, and because the canonical `ArticleShape` constrains
    `pulse:publicationType` by `sh:class`, a promoted Zenodo deposit carries
    one of the raw-file members. So the union is the only truthful answer, and
    enumerations get one shared module rather than one per layer.
    """
    graph = Graph()
    for path in paths:
        graph.parse(str(path), format="turtle")

    enumerations: list[Enumeration] = []
    for cls in set(graph.subjects(RDFS.subClassOf, SCHEMA.Enumeration)):
        curie = _curie(cls)
        if curie is None:
            continue
        enumeration = Enumeration(
            curie=curie,
            definition=_literal(graph.value(cls, SKOS.definition)),
        )
        for instance in graph.subjects(RDF.type, cls):
            member = _curie(instance)
            if member is None:
                continue
            enumeration.members.append(member)
            label = graph.value(instance, SKOS.prefLabel)
            if label is not None:
                enumeration.labels[member] = str(_literal(label))
        # `set()` above, and the ontology declares five disciplines twice, so
        # dedupe rather than trusting the file.
        enumeration.members = sorted(set(enumeration.members))
        if enumeration.members:
            enumerations.append(enumeration)

    return sorted(enumerations, key=lambda e: e.curie)


def _describe(shape: NodeShape) -> str:
    closed = "  [closed]" if shape.closed else ""
    lines = [f"{shape.local_name}  ->  {shape.target_class}{closed}"]
    for prop in shape.properties:
        bits = []
        if prop.datatype:
            bits.append(prop.datatype)
        if prop.node_class:
            bits.append(f"class {prop.node_class}")
        if prop.node_kind:
            bits.append(f"nodeKind {prop.node_kind}")
        card = f"[{prop.min_count or 0}..{prop.max_count if prop.max_count is not None else '*'}]"
        pattern = "  pattern" if prop.pattern else ""
        lines.append(f"    {prop.path:44s} {card:8s} {' '.join(bits)}{pattern}")
    lines.extend(f"    or: {' | '.join(alt)}" for alt in shape.identity_alternatives)
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual inspection
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ttl")
    parser.add_argument("--shape", help="only this shape's local name")
    args = parser.parse_args()

    for shape in read_shapes(Path(args.ttl)):
        if args.shape and shape.local_name != args.shape:
            continue
        print(_describe(shape))
        print()
