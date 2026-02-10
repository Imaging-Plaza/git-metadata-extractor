#!/usr/bin/env python3
"""
Semantic alignment test between TTL ontology (SHACL shapes) and JSON schemas.

Parses the TTL with rdflib and compares SHACL constraints against JSON Schema
definitions to catch drift between the two. Checks:

  1. Property alignment   - every sh:property path in a NodeShape has a
                            matching key in the JSON schema, and vice versa.
  2. Enum consistency     - DisciplineEnumeration, RepositoryTypeEnumeration,
                            and OrganizationTypeEnumeration instances in TTL
                            match the enum arrays in JSON schemas.
  3. Regex patterns       - sh:pattern values match JSON schema pattern values.
  4. Required fields      - sh:minCount 1 matches JSON schema required arrays.
  5. Datatype consistency - sh:datatype matches JSON schema type / pattern.
  6. Context mapping      - prefixed keys in JSON data resolve through the
                            JSON-LD context to valid sh:path IRIs.

Usage:
    python scripts/test_ttl_alignment.py

Requirements:
    pip install rdflib
"""

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from rdflib import Graph, Namespace, URIRef
    from rdflib.namespace import RDF, RDFS, XSD
except ImportError:
    print("Error: rdflib not installed. Run: pip install rdflib")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
JSON_DIR = BASE_DIR / "a-001"
STRICT_SCHEMA_DIR = JSON_DIR / "json-schema" / "strict"
TEST_OUTPUT_DIR = JSON_DIR / "test"
ONTOLOGY_FILE = BASE_DIR / "open-pulse-ontology-v2.0.0.ttl"

# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------
SH = Namespace("http://www.w3.org/ns/shacl#")
SCHEMA = Namespace("http://schema.org/")
ORG = Namespace("http://www.w3.org/ns/org#")
TIME = Namespace("http://www.w3.org/2006/time#")
PULSE = Namespace("https://open-pulse.epfl.ch/ontology#")
WD = Namespace("http://www.wikidata.org/entity/")

# ---------------------------------------------------------------------------
# Mapping: SHACL NodeShape local name  ->  JSON schema file
# ---------------------------------------------------------------------------
SHAPE_SCHEMA_MAP = {
    "PersonShape": "pulse_PersonShape.schema.json",
    "RepositoryShape": "pulse_RepositoryShape.schema.json",
    "OrganizationShape": "pulse_OrganizationShape.schema.json",
    "MembershipShape": "pulse_MembershipShape.schema.json",
    "ContributionShape": "pulse_ContributionShape.schema.json",
    "ArticleShape": "pulse_ArticleShape.schema.json",
}

# Fields that exist only in the JSON schema envelope (not in RDF/SHACL)
ENVELOPE_FIELDS = {"id", "type", "shacl", "identifiers", "idSource"}

# ---------------------------------------------------------------------------
# IRI  ->  prefixed name helpers
# ---------------------------------------------------------------------------
PREFIX_MAP = {
    str(SCHEMA): "schema:",
    str(ORG): "org:",
    str(TIME): "time:",
    str(PULSE): "pulse:",
    str(WD): "wd:",
    str(XSD): "xsd:",
}


def to_prefixed(iri: str) -> str:
    """Convert a full IRI to its prefixed form, e.g. schema:name."""
    for ns, prefix in PREFIX_MAP.items():
        if iri.startswith(ns):
            return prefix + iri[len(ns) :]
    return iri


# ---------------------------------------------------------------------------
# XSD datatype  ->  expected JSON schema type
# ---------------------------------------------------------------------------
XSD_TO_JSON = {
    str(XSD.string): "string",
    str(XSD.integer): "integer",
    str(XSD.date): "string",  # date stored as string with pattern
    str(XSD.dateTime): "string",  # dateTime stored as string with pattern
}

# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_graph(ttl_path: Path) -> Graph:
    g = Graph()
    g.parse(ttl_path, format="turtle")
    return g


def load_json(filepath: Path) -> dict:
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Extract SHACL constraints from TTL
# ---------------------------------------------------------------------------


def get_node_shapes(g: Graph) -> dict:
    """Return {shape_local_name: {targetClass, properties: [...]}}."""
    shapes = {}
    for shape in g.subjects(RDF.type, SH.NodeShape):
        local = str(shape).split("#")[-1]
        if local not in SHAPE_SCHEMA_MAP:
            continue

        target = None
        for t in g.objects(shape, SH.targetClass):
            target = str(t)

        props = []
        for prop_node in g.objects(shape, SH.property):
            prop_info = _extract_property(g, prop_node)
            if prop_info:
                props.append(prop_info)

        shapes[local] = {"targetClass": target, "properties": props}
    return shapes


def _extract_property(g: Graph, node) -> dict | None:
    """Extract property constraints from a sh:PropertyShape node."""
    path = None
    for p in g.objects(node, SH.path):
        path = str(p)

    if path is None:
        return None

    info: dict = {"path": path, "prefixed": to_prefixed(path)}

    for dt in g.objects(node, SH.datatype):
        info["datatype"] = str(dt)
    for pat in g.objects(node, SH.pattern):
        info["pattern"] = str(pat)
    for mc in g.objects(node, SH.minCount):
        info["minCount"] = int(mc)
    for mc in g.objects(node, SH.maxCount):
        info["maxCount"] = int(mc)
    for cls in g.objects(node, SH["class"]):
        info["class"] = str(cls)

    return info


# ---------------------------------------------------------------------------
# Extract enum instances from TTL
# ---------------------------------------------------------------------------

ENUM_CLASSES = {
    "DisciplineEnumeration": PULSE.DisciplineEnumeration,
    "RepositoryTypeEnumeration": PULSE.RepositoryTypeEnumeration,
    "OrganizationTypeEnumeration": PULSE.OrganizationTypeEnumeration,
}


def get_enum_instances(g: Graph) -> dict[str, set[str]]:
    """Return {enum_class_name: set_of_prefixed_instance_iris}."""
    result = {}
    for name, cls_uri in ENUM_CLASSES.items():
        instances = set()
        for inst in g.subjects(RDF.type, cls_uri):
            instances.add(to_prefixed(str(inst)))
        result[name] = instances
    return result


# ---------------------------------------------------------------------------
# Test 1 – Property alignment
# ---------------------------------------------------------------------------


def test_property_alignment(
    shapes: dict,
    schemas: dict[str, dict],
) -> list[dict]:
    """Check that TTL properties <-> JSON schema properties match."""
    issues = []
    for shape_name, shape_info in shapes.items():
        schema = schemas.get(shape_name)
        if schema is None:
            issues.append(
                {
                    "test": "property_alignment",
                    "shape": shape_name,
                    "severity": "error",
                    "message": f"No JSON schema found for {shape_name}",
                },
            )
            continue

        schema_keys = set(schema.get("properties", {}).keys()) - ENVELOPE_FIELDS
        ttl_keys = {p["prefixed"] for p in shape_info["properties"]}

        # Properties in TTL but missing from JSON schema
        for key in sorted(ttl_keys - schema_keys):
            issues.append(
                {
                    "test": "property_alignment",
                    "shape": shape_name,
                    "severity": "warning",
                    "message": f"Property '{key}' in TTL shape but missing from JSON schema",
                },
            )

        # Properties in JSON schema but missing from TTL
        for key in sorted(schema_keys - ttl_keys):
            issues.append(
                {
                    "test": "property_alignment",
                    "shape": shape_name,
                    "severity": "warning",
                    "message": f"Property '{key}' in JSON schema but missing from TTL shape",
                },
            )

    return issues


# ---------------------------------------------------------------------------
# Test 2 – Enum consistency
# ---------------------------------------------------------------------------

# Which JSON schema field contains each enum type, keyed by shape name
ENUM_FIELD_MAP = {
    "DisciplineEnumeration": ("RepositoryShape", "pulse:discipline"),
    "RepositoryTypeEnumeration": ("RepositoryShape", "pulse:repositoryType"),
    "OrganizationTypeEnumeration": ("OrganizationShape", "pulse:OrganizationType"),
}


def test_enum_consistency(
    ttl_enums: dict[str, set[str]],
    schemas: dict[str, dict],
) -> list[dict]:
    """Check enum values in TTL match enum arrays in JSON schemas."""
    issues = []

    for enum_name, (shape_name, field_name) in ENUM_FIELD_MAP.items():
        ttl_values = ttl_enums.get(enum_name, set())
        schema = schemas.get(shape_name)
        if schema is None:
            continue

        prop_def = schema.get("properties", {}).get(field_name, {})
        # Enum can be directly on the property or nested in items (for arrays)
        json_enum = prop_def.get("enum")
        if json_enum is None and "items" in prop_def:
            json_enum = prop_def["items"].get("enum")

        if json_enum is None:
            issues.append(
                {
                    "test": "enum_consistency",
                    "enum": enum_name,
                    "severity": "warning",
                    "message": f"No enum array found in JSON schema {shape_name}.{field_name}",
                },
            )
            continue

        json_values = set(json_enum)

        in_ttl_not_schema = sorted(ttl_values - json_values)
        in_schema_not_ttl = sorted(json_values - ttl_values)

        for v in in_ttl_not_schema:
            issues.append(
                {
                    "test": "enum_consistency",
                    "enum": enum_name,
                    "severity": "error",
                    "message": f"Value '{v}' in TTL but missing from JSON schema enum",
                },
            )
        for v in in_schema_not_ttl:
            issues.append(
                {
                    "test": "enum_consistency",
                    "enum": enum_name,
                    "severity": "error",
                    "message": f"Value '{v}' in JSON schema enum but not a TTL instance",
                },
            )

    return issues


# ---------------------------------------------------------------------------
# Test 3 – Regex pattern consistency
# ---------------------------------------------------------------------------


def test_regex_patterns(
    shapes: dict,
    schemas: dict[str, dict],
) -> list[dict]:
    """Check sh:pattern values match JSON schema pattern values."""
    issues = []

    for shape_name, shape_info in shapes.items():
        schema = schemas.get(shape_name)
        if schema is None:
            continue

        for prop in shape_info["properties"]:
            ttl_pattern = prop.get("pattern")
            if ttl_pattern is None:
                continue

            field_name = prop["prefixed"]
            prop_def = schema.get("properties", {}).get(field_name, {})
            json_pattern = prop_def.get("pattern")

            if json_pattern is None:
                issues.append(
                    {
                        "test": "regex_pattern",
                        "shape": shape_name,
                        "property": field_name,
                        "severity": "warning",
                        "message": (
                            f"TTL has pattern '{ttl_pattern}' but JSON schema "
                            f"'{field_name}' has no pattern"
                        ),
                    },
                )
                continue

            if ttl_pattern != json_pattern:
                issues.append(
                    {
                        "test": "regex_pattern",
                        "shape": shape_name,
                        "property": field_name,
                        "severity": "error",
                        "message": (
                            f"Pattern mismatch for '{field_name}': "
                            f"TTL='{ttl_pattern}' vs JSON='{json_pattern}'"
                        ),
                    },
                )

    return issues


# ---------------------------------------------------------------------------
# Test 4 – Required fields consistency
# ---------------------------------------------------------------------------


def test_required_fields(
    shapes: dict,
    schemas: dict[str, dict],
) -> list[dict]:
    """Check sh:minCount 1 matches JSON schema required arrays."""
    issues = []

    for shape_name, shape_info in shapes.items():
        schema = schemas.get(shape_name)
        if schema is None:
            continue

        json_required = set(schema.get("required", [])) - ENVELOPE_FIELDS

        for prop in shape_info["properties"]:
            field = prop["prefixed"]
            ttl_required = prop.get("minCount", 0) >= 1
            schema_required = field in json_required

            if ttl_required and not schema_required:
                issues.append(
                    {
                        "test": "required_fields",
                        "shape": shape_name,
                        "property": field,
                        "severity": "error",
                        "message": (
                            f"'{field}' has sh:minCount 1 in TTL but is NOT "
                            f"required in JSON schema"
                        ),
                    },
                )
            elif schema_required and not ttl_required:
                issues.append(
                    {
                        "test": "required_fields",
                        "shape": shape_name,
                        "property": field,
                        "severity": "warning",
                        "message": (
                            f"'{field}' is required in JSON schema but has NO "
                            f"sh:minCount 1 in TTL"
                        ),
                    },
                )

    return issues


# ---------------------------------------------------------------------------
# Test 5 – Datatype consistency
# ---------------------------------------------------------------------------


def test_datatype_consistency(
    shapes: dict,
    schemas: dict[str, dict],
) -> list[dict]:
    """Check sh:datatype matches JSON schema type."""
    issues = []

    for shape_name, shape_info in shapes.items():
        schema = schemas.get(shape_name)
        if schema is None:
            continue

        for prop in shape_info["properties"]:
            ttl_dt = prop.get("datatype")
            if ttl_dt is None:
                continue

            field = prop["prefixed"]
            prop_def = schema.get("properties", {}).get(field, {})
            if not prop_def:
                continue

            expected_json_type = XSD_TO_JSON.get(ttl_dt)
            if expected_json_type is None:
                continue

            json_type = prop_def.get("type")
            if json_type is None:
                continue

            # Normalise: JSON schema type can be a string or list
            if isinstance(json_type, list):
                json_types = set(json_type)
            else:
                json_types = {json_type}

            # SHACL allows multiple values by default unless sh:maxCount 1 is specified
            # If JSON type is "array", check the items type
            if "array" in json_types:
                items_type = prop_def.get("items", {}).get("type")
                if items_type == expected_json_type:
                    # Valid: array of the expected type (SHACL allows multiple values)
                    continue
                elif items_type:
                    # Array with wrong item type
                    issues.append(
                        {
                            "test": "datatype_consistency",
                            "shape": shape_name,
                            "property": field,
                            "severity": "error",
                            "message": (
                                f"Datatype mismatch for '{field}': TTL expects "
                                f"{to_prefixed(ttl_dt)} -> JSON '{expected_json_type}', "
                                f"but schema has array with items type={items_type}"
                            ),
                        },
                    )
                # If no items type specified, continue without error (could be a union type)
                continue

            # For non-array types, check if the expected type matches
            if expected_json_type not in json_types:
                issues.append(
                    {
                        "test": "datatype_consistency",
                        "shape": shape_name,
                        "property": field,
                        "severity": "error",
                        "message": (
                            f"Datatype mismatch for '{field}': TTL expects "
                            f"{to_prefixed(ttl_dt)} -> JSON '{expected_json_type}', "
                            f"but schema has type={json_type}"
                        ),
                    },
                )

    return issues


# ---------------------------------------------------------------------------
# Test 6 – Context mapping verification
# ---------------------------------------------------------------------------


def test_context_mapping(
    shapes: dict,
    context: dict,
) -> list[dict]:
    """Check that prefixed JSON keys resolve through context to SHACL paths."""
    issues = []

    # Build a set of all TTL property paths per shape
    all_ttl_paths: dict[str, set[str]] = {}
    for shape_name, shape_info in shapes.items():
        all_ttl_paths[shape_name] = {p["prefixed"] for p in shape_info["properties"]}

    # For each prefixed key used in the JSON data, resolve via context
    for shape_name, ttl_paths in all_ttl_paths.items():
        for prefixed_key in ttl_paths:
            # Check the key exists in the context (either as-is or short form)
            short_key = (
                prefixed_key.split(":")[-1] if ":" in prefixed_key else prefixed_key
            )
            found_in_context = prefixed_key in context or short_key in context
            if not found_in_context:
                issues.append(
                    {
                        "test": "context_mapping",
                        "shape": shape_name,
                        "property": prefixed_key,
                        "severity": "warning",
                        "message": (
                            f"Property '{prefixed_key}' from TTL shape has no "
                            f"mapping in JSON-LD context"
                        ),
                    },
                )

    return issues


# ---------------------------------------------------------------------------
# Load the JSON-LD context from build_jsonld.py
# ---------------------------------------------------------------------------


def load_jsonld_context() -> dict:
    """Import JSONLD_CONTEXT from build_jsonld.py."""
    build_script = SCRIPT_DIR / "build_jsonld.py"
    if not build_script.exists():
        return {}

    # Parse the context dict from the module without executing main()
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_jsonld", build_script)
    mod = importlib.util.module_from_spec(spec)

    # Prevent main() from running if guarded by __name__
    original_name = mod.__name__
    mod.__name__ = "build_jsonld"
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        mod.__name__ = original_name

    return getattr(mod, "JSONLD_CONTEXT", {})


# ---------------------------------------------------------------------------
# Summary + output
# ---------------------------------------------------------------------------


def print_summary(all_issues: list[dict]):
    """Print human-readable summary to console."""
    print("\n" + "=" * 60)
    print("TTL-SCHEMA SEMANTIC ALIGNMENT")
    print("=" * 60)

    errors = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]

    if not all_issues:
        print("\n  All alignment checks passed!")
    else:
        if errors:
            print(f"\n  Errors: {len(errors)}")
            for e in errors:
                print(f"    [{e['test']}] {e['message']}")
        if warnings:
            print(f"\n  Warnings: {len(warnings)}")
            for w in warnings:
                print(f"    [{w['test']}] {w['message']}")

    print()
    if errors:
        print(f"  {len(errors)} error(s), {len(warnings)} warning(s)")
    else:
        print(f"  0 errors, {len(warnings)} warning(s)")


def main():
    """Main entry point."""
    print("TTL-Schema Semantic Alignment Test")
    print(f"Ontology: {ONTOLOGY_FILE}")
    print(f"Schemas:  {STRICT_SCHEMA_DIR}")

    if not ONTOLOGY_FILE.exists():
        print(f"Error: Ontology file not found: {ONTOLOGY_FILE}")
        sys.exit(1)

    TEST_OUTPUT_DIR.mkdir(exist_ok=True)

    # Load TTL
    print("\nParsing TTL ontology...")
    g = load_graph(ONTOLOGY_FILE)
    print(f"  {len(g)} triples loaded")

    shapes = get_node_shapes(g)
    print(f"  {len(shapes)} node shapes extracted")

    ttl_enums = get_enum_instances(g)
    for name, vals in ttl_enums.items():
        print(f"  {name}: {len(vals)} instances")

    # Load JSON schemas (strict)
    print("\nLoading JSON schemas (strict)...")
    schemas: dict[str, dict] = {}
    for shape_name, schema_file in SHAPE_SCHEMA_MAP.items():
        schema_path = STRICT_SCHEMA_DIR / schema_file
        if schema_path.exists():
            schemas[shape_name] = load_json(schema_path)
        else:
            print(f"  Warning: schema not found: {schema_path}")
    print(f"  {len(schemas)} schemas loaded")

    # Load JSON-LD context
    print("\nLoading JSON-LD context...")
    context = load_jsonld_context()
    print(f"  {len(context)} context mappings")

    # Run tests
    print("\nRunning alignment tests...")
    all_issues: list[dict] = []

    all_issues.extend(test_property_alignment(shapes, schemas))
    all_issues.extend(test_enum_consistency(ttl_enums, schemas))
    all_issues.extend(test_regex_patterns(shapes, schemas))
    all_issues.extend(test_required_fields(shapes, schemas))
    all_issues.extend(test_datatype_consistency(shapes, schemas))
    all_issues.extend(test_context_mapping(shapes, context))

    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "ontology_file": str(ONTOLOGY_FILE.name),
        "schema_dir": str(STRICT_SCHEMA_DIR),
        "summary": {
            "total_issues": len(all_issues),
            "errors": len([i for i in all_issues if i["severity"] == "error"]),
            "warnings": len([i for i in all_issues if i["severity"] == "warning"]),
        },
        "issues": all_issues,
    }

    results_file = TEST_OUTPUT_DIR / "alignment_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to: {results_file}")

    print_summary(all_issues)

    errors = [i for i in all_issues if i["severity"] == "error"]
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
