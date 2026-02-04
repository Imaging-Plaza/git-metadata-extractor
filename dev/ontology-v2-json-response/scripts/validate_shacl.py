#!/usr/bin/env python3
"""
Validate JSON-LD data against SHACL shapes defined in the TTL ontology.

This script:
1. Loads JSON-LD data from a-001/test/jsonld_output.json
2. Loads SHACL shapes from open-pulse-ontology-v2.0.0.ttl
3. Validates the data graph against the shapes graph
4. Reports validation results

Usage:
    python scripts/validate_shacl.py

Requirements:
    pip install pyshacl rdflib
"""

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from pyshacl import validate
    from rdflib import Graph
except ImportError:
    print("Error: Required packages not installed.")
    print("Run: pip install pyshacl rdflib")
    sys.exit(1)


# Paths
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
JSON_DIR = BASE_DIR / "a-001"
TEST_OUTPUT_DIR = JSON_DIR / "test"
ONTOLOGY_FILE = BASE_DIR / "open-pulse-ontology-v2.0.0.ttl"
JSONLD_FILE = TEST_OUTPUT_DIR / "jsonld_output.json"


def load_jsonld_as_graph(jsonld_path: Path) -> Graph:
    """Load JSON-LD file and parse into RDF graph."""
    with open(jsonld_path, encoding="utf-8") as f:
        jsonld_data = json.load(f)

    graph = Graph()
    graph.parse(data=json.dumps(jsonld_data), format="json-ld")
    return graph


def load_shacl_shapes(ttl_path: Path) -> Graph:
    """Load SHACL shapes from TTL file."""
    shapes_graph = Graph()
    shapes_graph.parse(ttl_path, format="turtle")
    return shapes_graph


def run_shacl_validation(
    data_graph: Graph,
    shapes_graph: Graph,
    ont_graph: Graph = None,
) -> dict:
    """Run SHACL validation and return results.

    Args:
        data_graph: The data to validate
        shapes_graph: SHACL shapes
        ont_graph: Optional ontology graph to merge with data (for enumeration type definitions)
    """
    # Merge ontology definitions into data graph so SHACL can see enumeration types
    # This is needed because SHACL sh:class checks require the type triples in the data graph
    if ont_graph:
        data_graph = data_graph + ont_graph

    conforms, results_graph, results_text = validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
        abort_on_first=False,
        allow_infos=True,
        allow_warnings=True,
        meta_shacl=False,
        advanced=True,
        js=False,
        debug=False,
    )

    # Parse results
    violations = []
    warnings = []
    infos = []

    # Query the results graph for validation results
    results_query = """
    PREFIX sh: <http://www.w3.org/ns/shacl#>

    SELECT ?focusNode ?resultPath ?value ?message ?severity ?sourceShape
    WHERE {
        ?result a sh:ValidationResult ;
                sh:focusNode ?focusNode ;
                sh:resultSeverity ?severity .
        OPTIONAL { ?result sh:resultPath ?resultPath }
        OPTIONAL { ?result sh:value ?value }
        OPTIONAL { ?result sh:resultMessage ?message }
        OPTIONAL { ?result sh:sourceShape ?sourceShape }
    }
    """

    for row in results_graph.query(results_query):
        result_entry = {
            "focusNode": str(row.focusNode) if row.focusNode else None,
            "path": str(row.resultPath) if row.resultPath else None,
            "value": str(row.value) if row.value else None,
            "message": str(row.message) if row.message else None,
            "sourceShape": str(row.sourceShape) if row.sourceShape else None,
        }

        severity_str = str(row.severity)
        if "Violation" in severity_str:
            violations.append(result_entry)
        elif "Warning" in severity_str:
            warnings.append(result_entry)
        else:
            infos.append(result_entry)

    return {
        "conforms": conforms,
        "violations": violations,
        "warnings": warnings,
        "infos": infos,
        "results_text": results_text,
    }


def print_summary(results: dict):
    """Print validation summary."""
    print("\n" + "=" * 60)
    print("SHACL VALIDATION SUMMARY")
    print("=" * 60)

    if results["conforms"]:
        print("\n✅ Data CONFORMS to SHACL shapes!")
    else:
        print("\n❌ Data does NOT conform to SHACL shapes")

    print(f"\n  Violations: {len(results['violations'])}")
    print(f"  Warnings: {len(results['warnings'])}")
    print(f"  Infos: {len(results['infos'])}")

    if results["violations"]:
        print("\n--- VIOLATIONS ---")
        for i, v in enumerate(results["violations"][:10], 1):  # Show first 10
            print(f"\n{i}. Focus Node: {v['focusNode']}")
            if v["path"]:
                print(f"   Path: {v['path']}")
            if v["value"]:
                print(f"   Value: {v['value']}")
            if v["message"]:
                print(f"   Message: {v['message']}")
            if v["sourceShape"]:
                print(f"   Shape: {v['sourceShape']}")

        if len(results["violations"]) > 10:
            print(f"\n   ... and {len(results['violations']) - 10} more violations")

    if results["warnings"]:
        print("\n--- WARNINGS ---")
        for i, w in enumerate(results["warnings"][:5], 1):  # Show first 5
            print(f"\n{i}. {w['message']}")
            print(f"   Node: {w['focusNode']}")

        if len(results["warnings"]) > 5:
            print(f"\n   ... and {len(results['warnings']) - 5} more warnings")


def main():
    """Main entry point."""
    print("SHACL Validation")
    print(f"Ontology: {ONTOLOGY_FILE}")
    print(f"JSON-LD: {JSONLD_FILE}")

    # Check files exist
    if not ONTOLOGY_FILE.exists():
        print(f"Error: Ontology file not found: {ONTOLOGY_FILE}")
        sys.exit(1)

    if not JSONLD_FILE.exists():
        print(f"Error: JSON-LD file not found: {JSONLD_FILE}")
        print("Run build_jsonld.py first to generate the JSON-LD file.")
        sys.exit(1)

    # Create output directory
    TEST_OUTPUT_DIR.mkdir(exist_ok=True)

    # Load graphs
    print("\nLoading SHACL shapes from TTL...")
    shapes_graph = load_shacl_shapes(ONTOLOGY_FILE)
    print(f"  Loaded {len(shapes_graph)} triples")

    # Load ontology again for merging with data (to include enumeration type definitions)
    print("\nLoading ontology definitions...")
    ont_graph = Graph()
    ont_graph.parse(ONTOLOGY_FILE, format="turtle")
    print(f"  Loaded {len(ont_graph)} triples (for enumeration class membership)")

    print("\nLoading JSON-LD data...")
    data_graph = load_jsonld_as_graph(JSONLD_FILE)
    print(f"  Loaded {len(data_graph)} triples")

    # Run validation
    print("\nRunning SHACL validation...")
    results = run_shacl_validation(data_graph, shapes_graph, ont_graph)

    # Save results
    output_results = {
        "timestamp": datetime.now().isoformat(),
        "ontology_file": str(ONTOLOGY_FILE.name),
        "jsonld_file": str(JSONLD_FILE.name),
        "conforms": results["conforms"],
        "summary": {
            "violations": len(results["violations"]),
            "warnings": len(results["warnings"]),
            "infos": len(results["infos"]),
        },
        "violations": results["violations"],
        "warnings": results["warnings"],
        "infos": results["infos"],
    }

    results_file = TEST_OUTPUT_DIR / "shacl_validation_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(output_results, f, indent=2)
    print(f"\nResults saved to: {results_file}")

    # Save full text report
    text_report_file = TEST_OUTPUT_DIR / "shacl_validation_report.txt"
    with open(text_report_file, "w", encoding="utf-8") as f:
        f.write(results["results_text"])
    print(f"Full report saved to: {text_report_file}")

    # Print summary
    print_summary(results)

    # Exit with error code if validation failed
    sys.exit(0 if results["conforms"] else 1)


if __name__ == "__main__":
    main()
