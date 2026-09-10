# ruff: noqa: INP001
"""Emit Pydantic models from the SHACL shapes.

Replaces the hand-written JSON Schema tier: 36 files kept in three
byte-identical copies, transcribed by hand from shapes that already carry every
constraint. `sh:closed`, `sh:datatype`, `sh:pattern`, `sh:minCount`,
`sh:maxCount` and `sh:or` map onto Pydantic directly.

Run through `prepare_ontology.py` first — this reads the patched submodule and
refuses to guess:

    just ontology-prepare
    python scripts/v2/generate_from_ontology.py
    python scripts/v2/generate_from_ontology.py --check    # CI drift gate

One model per node shape, one module per layer. The `sh:or` blocks become a
model validator, so the identity hierarchy ("ORCID or at least one profile")
is enforced by construction rather than described in a docstring — which is
what the hand-written schemas did with a prose `description`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ontology_reader import (  # noqa: E402
    PREFIXES,
    Enumeration,
    NodeShape,
    PropertyConstraint,
    read_enumerations,
    read_shapes,
    read_version,
)
from prepare_ontology import (  # noqa: E402
    DEFINITION_FILES,
    ENUMERATION_FILES,
    ONTOLOGY_DIR,
    SHAPE_FILES,
)

OUT_DIR = REPO_ROOT / "git_metadata_extractor" / "schema" / "generated"
ENUM_MODULE = "enumerations"
CONTEXT_FILE = "context.jsonld"
#: Above this, an enumeration is emitted as a `frozenset` and its type alias
#: degrades to `str`. `pulse:DisciplineEnumeration` has 1606 members: a
#: `Literal` that wide bloats the module past 1600 lines and makes type
#: checkers crawl, for a vocabulary that legitimately churns with each EPFL
#: Graph refresh. Every other enumeration has at most 15 members.
_LITERAL_MAX_MEMBERS = 64

#: xsd datatype -> (python annotation, import needed)
_DATATYPES: dict[str, tuple[str, str | None]] = {
    "xsd:string": ("str", None),
    "xsd:integer": ("int", None),
    "xsd:decimal": ("Decimal", "from decimal import Decimal"),
    "xsd:boolean": ("bool", None),
    "xsd:date": ("date", "from datetime import date"),
    "xsd:dateTime": ("datetime", "from datetime import datetime"),
    "xsd:anyURI": ("str", None),
}


def _identifier(path: str) -> str:
    """`pulse:githubUsername` -> `pulse_githubUsername`, matching the old models."""
    return path.replace(":", "_").replace("-", "_").replace(".", "_")


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _annotation(
    prop: PropertyConstraint,
    enums: dict[str, Enumeration],
) -> tuple[str, set[str]]:
    """Python annotation for one constraint, plus any imports it needs."""
    imports: set[str] = set()
    if prop.datatype and prop.datatype in _DATATYPES:
        base, needed = _DATATYPES[prop.datatype]
        if needed:
            imports.add(needed)
    elif prop.node_class in enums:
        # The shapes constrain enumerated properties with `sh:class`, never
        # `sh:in`, so the permitted values live in the enumeration files rather
        # than in the shape. Flattening these to `str` — which this generator
        # used to do — dropped the vocabulary entirely: nothing stopped a
        # `pulse:repositoryType` of `"pulse:Banana"`.
        enumeration = enums[prop.node_class]
        base = enumeration.alias_name
        imports.add(
            f"from git_metadata_extractor.schema.generated.{ENUM_MODULE} import "
            f"{enumeration.alias_name}",
        )
    else:
        # A reference is carried as the target's IRI. Keeping it `str` rather
        # than a nested model is deliberate: the graph is a flat node list, and
        # nesting would imply containment the RDF does not have.
        base = "str"

    if not prop.single_valued:
        # The pattern has to move *inside* the list, or it silently applies to
        # nothing. `sh:pattern` on a multi-valued property constrains each
        # value, and several shapes are multi-valued only because they omit
        # `sh:maxCount 1` — dropping the pattern there would lose the ORCID and
        # handle formats entirely.
        if prop.pattern:
            imports.add("from typing import Annotated")
            base = f'list[Annotated[{base}, Field(pattern="{_escape(prop.pattern)}")]]'
        else:
            base = f"list[{base}]"
    if not prop.required:
        base = f"{base} | None"
    return base, imports


def _field(prop: PropertyConstraint) -> str:
    args: list[str] = ["..." if prop.required else "None"]
    args.append(f'alias="{prop.path}"')
    if prop.pattern and prop.single_valued:
        args.append(f'pattern="{_escape(prop.pattern)}"')
    description = prop.description or prop.name
    if description:
        clean = " ".join(str(description).split())
        args.append(f'description="{_escape(clean)}"')
    return f"Field({', '.join(args)})"


def _class_name(shape: NodeShape) -> str:
    return shape.local_name.removesuffix("Shape") + "Model"


def _render_shape(
    shape: NodeShape,
    enums: dict[str, Enumeration],
) -> tuple[str, set[str]]:
    imports: set[str] = set()
    lines = [f"class {_class_name(shape)}(BaseModel):"]

    doc = [f'    """{shape.target_class}']
    if shape.closed:
        doc.append("")
        doc.append("    Closed shape: unknown properties are rejected.")
    if shape.identity_alternatives:
        doc.append("")
        doc.append("    Identity requires at least one of:")
        doc.extend(
            f"      - {' or '.join(alt)}" for alt in shape.identity_alternatives
        )
    doc.append('    """')
    lines.extend(doc)

    lines.append("")
    lines.append("    model_config = ConfigDict(")
    lines.append("        populate_by_name=True,")
    if shape.closed:
        lines.append('        extra="forbid",')
    lines.append("    )")
    lines.append("")

    if not shape.properties:
        lines.append("    # shape declares no properties")

    for prop in shape.properties:
        annotation, needed = _annotation(prop, enums)
        imports |= needed
        lines.append(f"    {_identifier(prop.path)}: {annotation} = {_field(prop)}")

    if shape.identity_alternatives:
        alts = [sorted(set(alt)) for alt in shape.identity_alternatives]
        flat = sorted({p for alt in alts for p in alt})
        fields = ", ".join(f'"{_identifier(p)}"' for p in flat)
        lines.extend(
            [
                "",
                '    @model_validator(mode="after")',
                '    def _identity_present(self) -> "' + _class_name(shape) + '":',
                '        """At least one identifying property must be set.',
                "",
                "        Straight from the shape's `sh:or`. The hand-written JSON",
                "        Schemas expressed this as an `anyOf` plus a prose description;",
                "        here it fails at construction.",
                '        """',
                f"        candidates = ({fields},)",
                "        if not any(getattr(self, name, None) for name in candidates):",
                '            joined = ", ".join(candidates)',
                "            message = (",
                f'                "{_class_name(shape)} requires at least one of: " + joined',
                "            )",
                "            raise ValueError(message)",
                "        return self",
            ],
        )

    return "\n".join(lines), imports


def render_layer(
    layer: str,
    shapes: list[NodeShape],
    enums: dict[str, Enumeration],
) -> str:
    bodies: list[str] = []
    imports: set[str] = set()
    for shape in shapes:
        body, needed = _render_shape(shape, enums)
        bodies.append(body)
        imports |= needed

    header = [
        '"""Generated from the Open Pulse SHACL shapes. Do not edit by hand.',
        "",
        f"    layer:  {layer}",
        f"    source: {SHAPE_FILES[layer]}",
        "",
        "Regenerate with:",
        "",
        "    just ontology-prepare",
        "    python scripts/v2/generate_from_ontology.py",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]
    header.extend(sorted(imports))
    if imports:
        header.append("")
    header.append("from pydantic import BaseModel, ConfigDict, Field, model_validator")
    header.append("")
    header.append("")

    # An explicit target-class -> model mapping. The class is already in each
    # model's docstring, but a consumer that parses a docstring to find it is
    # one refactor away from silently finding nothing — and the first consumer,
    # `unify/policy.py`, needs it to know that `schema:author` is capped on
    # `pulse:Contribution` and unbounded on `schema:ScholarlyArticle`. That is
    # the only shape-dependent cardinality in the canonical layer, and it is
    # exactly the kind that a per-property table gets wrong.
    mapping = [
        "#: `sh:targetClass` -> the model generated for its shape.",
        "MODELS_BY_TARGET_CLASS: dict[str, type[BaseModel]] = {",
        *(
            f'    "{shape.target_class}": {_class_name(shape)},'
            for shape in shapes
        ),
        "}",
        "",
        "",
        "#: Properties this layer gives `sh:maxCount 1`, per target class. Read off",
        "#: the shapes at generation time so no consumer has to re-derive it.",
        "SINGLE_VALUED_BY_TARGET_CLASS: dict[str, frozenset[str]] = {",
        *(
            f'    "{shape.target_class}": frozenset({{'
            + ", ".join(
                f'"{prop.path}"'
                for prop in sorted(shape.properties, key=lambda p: p.path)
                if prop.max_count == 1
            )
            + "}),"
            for shape in shapes
        ),
        "}",
    ]
    return (
        "\n".join(header)
        + "\n\n\n".join(bodies)
        + "\n\n\n"
        + "\n".join(mapping)
        + "\n"
    )


def _screaming(name: str) -> str:
    """`SpaceRuntimeStatus` -> `SPACE_RUNTIME_STATUS`."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()


def render_enumerations(enumerations: list[Enumeration]) -> str:
    """One shared module for every enumeration, across all layers.

    Not one module per layer, unlike the shapes: enumeration classes are
    extended across files, so a per-layer split would emit the same type twice
    with different members. See `read_enumerations`.
    """
    lines = [
        '"""Generated from the Open Pulse enumeration instances. Do not edit by hand.',
        "",
        "    sources: " + ", ".join(ENUMERATION_FILES),
        "",
        "Read as one merged graph, because enumeration classes are extended across",
        "layers: `pulse:PublicationTypeEnumeration` is declared with seven members in",
        "the canonical file and gains eight more (the Zenodo upload types) from the raw",
        "file. Neither file is authoritative on its own.",
        "",
        "Regenerate with:",
        "",
        "    just ontology-prepare",
        "    python scripts/v2/generate_from_ontology.py",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Literal",
        "",
    ]

    for enumeration in enumerations:
        alias = enumeration.alias_name
        const = _screaming(alias)
        lines.append("")
        if enumeration.definition:
            wrapped = " ".join(str(enumeration.definition).split())
            lines.append(f"#: {wrapped}")
        lines.append(f"#: `{enumeration.curie}` — {len(enumeration.members)} members.")

        if len(enumeration.members) <= _LITERAL_MAX_MEMBERS:
            lines.append(f"{alias} = Literal[")
            lines.extend(f'    "{_escape(m)}",' for m in enumeration.members)
            lines.append("]")
        else:
            lines.extend(
                [
                    f"#: Too wide for a `Literal` ({len(enumeration.members)} members,",
                    f"#: threshold {_LITERAL_MAX_MEMBERS}), so the alias degrades to",
                    f"#: `str` and the vocabulary is enforceable via {const}_MEMBERS",
                    "#: at runtime instead of by a type checker.",
                    f"{alias} = str",
                ],
            )

        lines.append("")
        lines.append(f"{const}_MEMBERS: frozenset[str] = frozenset(")
        lines.append("    {")
        lines.extend(f'        "{_escape(m)}",' for m in enumeration.members)
        lines.append("    },")
        lines.append(")")

        # Labels are the human-readable half of what the hand-written schemas
        # carried as prose. Skipped for the oversized enumeration: 1606 label
        # entries is noise, and the disciplines are already labelled upstream.
        if enumeration.labels and len(enumeration.members) <= _LITERAL_MAX_MEMBERS:
            lines.append("")
            lines.append(f"{const}_LABELS: dict[str, str] = {{")
            lines.extend(
                f'    "{_escape(m)}": "{_escape(enumeration.labels[m])}",'
                for m in enumeration.members
                if m in enumeration.labels
            )
            lines.append("}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _term_definition(
    constraints: list[PropertyConstraint],
    enums: dict[str, Enumeration],
) -> dict[str, str] | None:
    """The JSON-LD term definition for one property, or None if it needs none.

    A context is global: one definition per property, however many shapes use
    it. Eight properties are defined by more than one shape with *disagreeing
    cardinality* — `schema:name` appears in 17 shapes, `sh:maxCount 1` in some
    and absent in others. The types always agree, so only the container is in
    question, and the answer is `@set` whenever any shape permits several
    values: `@set` renders a lone value as a one-element array, which is
    lossless, whereas guessing scalar for a property that turns out to be
    repeated changes what the document means.
    """
    definition: dict[str, str] = {}

    reference = any(c.is_reference for c in constraints)
    enumerated = any(c.node_class in enums for c in constraints)
    datatypes = {c.datatype for c in constraints if c.datatype}

    if reference or enumerated:
        # Enumeration members are IRIs (`pulse:Software`), so they compact and
        # expand as ids, exactly like a reference to another node.
        definition["@type"] = "@id"
    elif len(datatypes) == 1:
        datatype = next(iter(datatypes))
        # `xsd:string` is JSON-LD's default for a plain literal; stating it adds
        # bytes and says nothing.
        if datatype != "xsd:string":
            definition["@type"] = datatype

    if any(c.max_count != 1 for c in constraints):
        definition["@container"] = "@set"

    return definition or None


def render_context(
    shapes_by_layer: dict[str, list[NodeShape]],
    enums: dict[str, Enumeration],
    version: str | None,
) -> str:
    """The JSON-LD context, derived from the same IR as the models.

    Replaces a hand-maintained file that encoded three facts the shapes already
    carry: `@type: @id` for references, `@container: @set` for multi-valued
    properties, and `@type: xsd:*` for typed literals. Hand-maintaining that is
    how a context drifts from the shapes it is supposed to describe.
    """
    by_path: dict[str, list[PropertyConstraint]] = {}
    for shapes in shapes_by_layer.values():
        for shape in shapes:
            for prop in shape.properties:
                by_path.setdefault(prop.path, []).append(prop)

    context: dict[str, object] = dict(PREFIXES)
    for path in sorted(by_path):
        definition = _term_definition(by_path[path], enums)
        if definition is not None:
            context[path] = definition

    payload = {
        "context_version": version or "unknown",
        "ontology_version": f"open-pulse-ontology-{version or 'unknown'}",
        "_generated": (
            "Generated from the Open Pulse SHACL shapes by "
            "scripts/v2/generate_from_ontology.py. Do not edit by hand; run "
            "just ontology-models-generate."
        ),
        "@context": context,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _shapes_for(layer: str) -> list[NodeShape]:
    return read_shapes(ONTOLOGY_DIR / SHAPE_FILES[layer])


def _format(paths: list[Path]) -> None:
    """Run ruff over the output so generated code matches project style."""
    for args in (
        ["ruff", "check", "--fix", "--quiet", *[str(p) for p in paths]],
        ["ruff", "format", "--quiet", *[str(p) for p in paths]],
    ):
        subprocess.run(args, cwd=REPO_ROOT, check=False, capture_output=True)  # noqa: S603


def _check(target: Path, rendered: str, layer: str, drift: list[str]) -> None:
    """Compare post-format, since that is what lands on disk."""
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    tmp = OUT_DIR / f".{layer}.check.py"
    tmp.write_text(rendered, encoding="utf-8")
    _format([tmp])
    formatted = tmp.read_text(encoding="utf-8")
    tmp.unlink(missing_ok=True)
    if formatted != current:
        drift.append(f"{layer}.py")


def generate(*, check_only: bool) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    drift: list[str] = []

    enumerations = read_enumerations(
        [ONTOLOGY_DIR / name for name in ENUMERATION_FILES],
    )
    enums = {e.curie: e for e in enumerations}

    enum_target = OUT_DIR / f"{ENUM_MODULE}.py"
    enum_rendered = render_enumerations(enumerations)
    if check_only:
        _check(enum_target, enum_rendered, ENUM_MODULE, drift)
    else:
        enum_target.write_text(enum_rendered, encoding="utf-8")
        written.append(enum_target)
        print(
            f"  {len(enumerations):3d} enumerations -> "
            f"{enum_target.relative_to(REPO_ROOT)}",
        )

    shapes_by_layer = {layer: _shapes_for(layer) for layer in SHAPE_FILES}

    version = read_version(ONTOLOGY_DIR / DEFINITION_FILES[0])
    context_target = OUT_DIR / CONTEXT_FILE
    context_rendered = render_context(shapes_by_layer, enums, version)
    if check_only:
        current = (
            context_target.read_text(encoding="utf-8")
            if context_target.exists()
            else ""
        )
        if context_rendered != current:
            drift.append(CONTEXT_FILE)
    else:
        context_target.write_text(context_rendered, encoding="utf-8")
        terms = context_rendered.count('": {')
        print(
            f"  {terms:3d} terms       -> "
            f"{context_target.relative_to(REPO_ROOT)} ({version})",
        )

    for layer, shapes in shapes_by_layer.items():
        rendered = render_layer(layer, shapes, enums)
        target = OUT_DIR / f"{layer}.py"

        if check_only:
            _check(target, rendered, layer, drift)
            continue

        target.write_text(rendered, encoding="utf-8")
        written.append(target)
        print(f"  {len(shapes):3d} shapes -> {target.relative_to(REPO_ROOT)}")

    init = OUT_DIR / "__init__.py"
    if not check_only:
        init.write_text(
            '"""Generated Pydantic models, one module per ontology layer."""\n',
            encoding="utf-8",
        )
        written.append(init)
        _format(written)
        print(f"generated {len(written)} module(s)")
        return 0

    if drift:
        print("generated models are stale: " + ", ".join(drift))
        print("run: just ontology-prepare && python scripts/v2/generate_from_ontology.py")
        return 1
    print("generated models are up to date")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if output is stale")
    args = parser.parse_args()
    if not ONTOLOGY_DIR.is_dir():
        print(
            "error: ontology not prepared. Run: just ontology-prepare",
            file=sys.stderr,
        )
        return 1
    return generate(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
