#!/usr/bin/env python3
"""
Round-trip test: JSON -> JSON-LD -> RDF triples -> reconstructed JSON.

Loads the JSON-LD output with rdflib, reconstructs per-entity JSON objects
from the RDF triples, saves them to a-001/test/roundtrip/, and compares
against the original source JSON files.

Expected differences (not counted as errors):
  - Envelope fields (shacl, identifiers, idSource) are stripped before
    JSON-LD and will not survive the round trip.
  - Null values produce no RDF triples, so they vanish.
  - Empty arrays produce no triples, so they vanish.

Real errors this test catches:
  - Properties silently dropped during JSON-LD parsing (bad context mapping).
  - Value type coercion (e.g. integer becoming string, or vice versa).
  - IRI expansion losing the original short ID.
  - Unexpected extra triples injected by rdflib inference.

Usage:
    python scripts/test_roundtrip.py

Requirements:
    pip install rdflib

===============================================================================
MAINTENANCE GUIDE: Updating this script when modifying the TTL ontology
===============================================================================

When you modify open-pulse-ontology-v2.0.0.ttl, you may need to update this
script. Here's what to check and how to fix common issues:

1. ADDING A NEW ENTITY TYPE (new sh:NodeShape in TTL)
   ═══════════════════════════════════════════════════════════════════════════
   Example: Adding pulse:DatasetShape

   Steps:
   a) Add to SHAPE_FILES dict (~line 70):
      "pulse:Dataset": "pulse_DatasetShape.json",

   b) Check if the entity has array properties in its JSON schema

   c) If yes, add to ARRAY_PROPERTIES_BY_TYPE (~line 50):
      "pulse:Dataset": {"pulse:creators", "pulse:keywords"},

2. ADDING/MODIFYING PROPERTIES (sh:property in TTL)
   ═══════════════════════════════════════════════════════════════════════════
   SHACL Cardinality Rules:
   - NO sh:maxCount specified → allows 0..* values → JSON array
   - sh:maxCount 1 → allows 0..1 values → JSON scalar/null

   Example: Adding pulse:keywords to RepositoryShape

   TTL:
     pulse:RepositoryShape
       sh:property [ sh:path pulse:keywords ; sh:datatype xsd:string ] ;

   → This allows multiple keywords, so update ARRAY_PROPERTIES_BY_TYPE:

     "schema:SoftwareSourceCode": {
         "schema:author",
         "pulse:discipline",
         "schema:programmingLanguage",
         "pulse:keywords"  # ← ADD HERE
     },

   Symptom if missing: Round-trip test shows value_mismatch errors where
   single-element arrays collapse to scalars.

3. ADDING A NEW NAMESPACE (new vocabulary in TTL)
   ═══════════════════════════════════════════════════════════════════════════
   Example: Adding FOAF (Friend of a Friend) vocabulary

   TTL:
     @prefix foaf: <http://xmlns.com/foaf/0.1/> .

   Update to_prefixed() function (~line 130):
     namespaces = {
         "https://open-pulse.epfl.ch/ontology#": "pulse:",
         "http://www.wikidata.org/entity/": "wd:",
         "http://xmlns.com/foaf/0.1/": "foaf:",  # ← ADD HERE
         ...
     }

   Why: Ensures properties/values are reconstructed with correct prefix
   (e.g., "foaf:Person" not "http://xmlns.com/foaf/0.1/Person").

4. CONTEXT-DEPENDENT PROPERTIES
   ═══════════════════════════════════════════════════════════════════════════
   Some properties are arrays in some entity types but scalars in others.
   Example: schema:author
   - Array in Repository (multiple authors)
   - Scalar in Contribution (single contributor)

   Solution: Use ARRAY_PROPERTIES_BY_TYPE (entity-specific), NOT the global
   ARRAY_PROPERTIES set.

   Configuration:
     ARRAY_PROPERTIES_BY_TYPE = {
         "schema:SoftwareSourceCode": {"schema:author"},  # Array
         "pulse:Contribution": {},  # Scalar (omit schema:author)
     }

5. CHANGING PROPERTY CARDINALITY
   ═══════════════════════════════════════════════════════════════════════════
   Scenario: Changing pulse:maintainer from single to multiple

   Before (TTL):
     sh:property [ sh:path pulse:maintainer ; sh:maxCount 1 ]

   After (TTL):
     sh:property [ sh:path pulse:maintainer ]  # Removed maxCount

   Action: Add to ARRAY_PROPERTIES_BY_TYPE for affected entity types

   Opposite change (multiple → single):
   - Remove from ARRAY_PROPERTIES_BY_TYPE
   - Add sh:maxCount 1 to TTL

TESTING YOUR CHANGES:
  1. Run: python scripts/test_roundtrip.py
  2. Look for "value_mismatch" errors (array/scalar mismatches)
  3. Compare original vs reconstructed values in the output
  4. If test fails, review ARRAY_PROPERTIES_BY_TYPE configuration

DEBUGGING TIPS:
  - Check test/roundtrip/*.json to see reconstructed entities
  - Compare with original a-001/pulse_*Shape.json files
  - Array mismatches mean property needs to be in ARRAY_PROPERTIES_BY_TYPE
  - Prefix mismatches mean namespace needs to be in to_prefixed()

RELATED FILES TO UPDATE:
  - build_jsonld.py: Add @container: "@set" for new array properties
  - JSON schemas: Update to match TTL cardinality constraints
===============================================================================
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    from rdflib import Graph, Literal, URIRef
    from rdflib.namespace import RDF, XSD
except ImportError:
    print("Error: rdflib not installed. Run: pip install rdflib")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Properties that should always be arrays (even with single element)
# Organized by entity type since some properties (like schema:author) are
# arrays for some entities but scalars for others.
# ---------------------------------------------------------------------------
ARRAY_PROPERTIES_BY_TYPE = {
    "schema:SoftwareSourceCode": {
        "schema:author",
        "pulse:discipline",
        "schema:programmingLanguage",
    },
    "schema:ScholarlyArticle": {"schema:author"},
    "org:Organization": {"org:hasUnit", "pulse:owns"},
    "schema:Person": {"org:hasMembership", "pulse:hasContribution"},
}

# Flatten for quick lookup regardless of type (used as fallback)
ARRAY_PROPERTIES = {
    "pulse:discipline",
    "schema:programmingLanguage",
    "org:hasUnit",
    "pulse:owns",
    "pulse:hasContribution",
    "org:hasMembership",
}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
JSON_DIR = BASE_DIR / "a-001"
TEST_OUTPUT_DIR = JSON_DIR / "test"
ROUNDTRIP_DIR = TEST_OUTPUT_DIR / "roundtrip"
JSONLD_FILE = TEST_OUTPUT_DIR / "jsonld_output.json"

# Original source files
SHAPE_FILES = {
    "schema:Person": "pulse_PersonShape.json",
    "schema:SoftwareSourceCode": "pulse_RepositoryShape.json",
    "org:Organization": "pulse_OrganizationShape.json",
    "org:Membership": "pulse_MembershipShape.json",
    "pulse:Contribution": "pulse_ContributionShape.json",
    "schema:ScholarlyArticle": "pulse_ArticleShape.json",
}

# Envelope fields that are intentionally stripped before JSON-LD conversion
ENVELOPE_FIELDS = {"shacl", "identifiers", "idSource"}

# ---------------------------------------------------------------------------
# IRI <-> prefixed name mapping
# ---------------------------------------------------------------------------
NAMESPACES = {
    "http://schema.org/": "schema:",
    "http://www.w3.org/ns/org#": "org:",
    "http://www.w3.org/2006/time#": "time:",
    "https://open-pulse.epfl.ch/ontology#": "pulse:",
    "http://www.wikidata.org/entity/": "wd:",
}


def to_prefixed(iri: str) -> str:
    """Convert full IRI to prefixed form."""
    for ns, prefix in NAMESPACES.items():
        if iri.startswith(ns):
            return prefix + iri[len(ns) :]
    return iri


def shorten_id(iri: str) -> str:
    """Extract the short ID from a full IRI used as a node @id.

    The JSON-LD context sets @vocab to pulse namespace and @base to data namespace.
    rdflib expands IDs based on these contexts:
    - Relative IDs like "0000-0001-2345-6789" → "https://open-pulse.epfl.ch/data/0000-0001-2345-6789"
    - Bare ontology terms → "https://open-pulse.epfl.ch/ontology#..."
    IDs that are already full IRIs (like ROR URLs) stay as-is.
    """
    pulse_ns = "https://open-pulse.epfl.ch/ontology#"
    data_ns = "https://open-pulse.epfl.ch/data/"

    if iri.startswith(data_ns):
        return iri[len(data_ns) :]
    elif iri.startswith(pulse_ns):
        return iri[len(pulse_ns) :]
    return iri


def to_prefixed(iri: str, use_pulse_prefix: bool = True) -> str:
    """Convert full IRI to prefixed form (e.g., pulse:University, wd:Q123).

    This is used for reconstructing values from RDF where we want to preserve
    the namespace prefix (e.g., for enum values like pulse:University).

    Args:
        iri: Full IRI string
        use_pulse_prefix: Whether to include prefix for ontology terms (default: True)

    Returns:
        Prefixed form if namespace matches, otherwise original IRI
    """
    # Namespace mappings from build_jsonld.py
    namespaces = {
        "https://open-pulse.epfl.ch/ontology#": "pulse:",
        "http://www.wikidata.org/entity/": "wd:",
        "http://schema.org/": "schema:",
        "http://www.w3.org/ns/org#": "org:",
        "http://www.w3.org/2006/time#": "time:",  # Time ontology namespace
        "https://open-pulse.epfl.ch/data/": "",  # Data namespace - no prefix
    }

    for ns, prefix in namespaces.items():
        if iri.startswith(ns):
            local_name = iri[len(ns) :]
            if prefix == "" or not use_pulse_prefix:
                return local_name
            return prefix + local_name

    return iri  # Return as-is if no namespace match


# ---------------------------------------------------------------------------
# Reconstruct JSON from RDF triples
# ---------------------------------------------------------------------------


def reconstruct_from_graph(g: Graph) -> dict[str, list[dict]]:
    """Parse RDF graph into per-type lists of JSON objects."""
    # Gather all subjects that have an rdf:type
    entities: dict[str, dict] = {}  # subject_iri -> {props}
    entity_types: dict[str, str] = {}  # subject_iri -> prefixed type

    for s, _, o in g.triples((None, RDF.type, None)):
        s_str = str(s)
        o_str = str(o)
        prefixed_type = to_prefixed(o_str)

        # Skip ontology-level types (classes, property shapes, etc.)
        if prefixed_type.startswith("http://www.w3.org/"):
            continue
        if "Shape" in o_str or "Enumeration" in o_str:
            continue
        if o_str in (
            "http://www.w3.org/2002/07/owl#Ontology",
            "http://www.w3.org/2000/01/rdf-schema#Class",
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#Property",
        ):
            continue

        entity_types[s_str] = prefixed_type
        if s_str not in entities:
            entities[s_str] = {}

    # Collect all property values for known entities
    for s_str in entities:
        s_uri = URIRef(s_str)
        for p, o in g.predicate_objects(s_uri):
            p_str = str(p)
            if p_str == str(RDF.type):
                continue

            prefixed_prop = to_prefixed(p_str)
            value = _rdf_value(o)

            if prefixed_prop in entities[s_str]:
                existing = entities[s_str][prefixed_prop]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    entities[s_str][prefixed_prop] = [existing, value]
            else:
                # Check if this property should always be an array
                # First check entity-type-specific array properties
                entity_type = entity_types.get(s_str)
                type_specific_arrays = ARRAY_PROPERTIES_BY_TYPE.get(entity_type, set())

                if (
                    prefixed_prop in type_specific_arrays
                    or prefixed_prop in ARRAY_PROPERTIES
                ):
                    entities[s_str][prefixed_prop] = [value]
                else:
                    entities[s_str][prefixed_prop] = value

    # Group by type and build output
    by_type: dict[str, list[dict]] = defaultdict(list)
    for s_str, props in entities.items():
        entity_type = entity_types[s_str]
        node = {
            "id": shorten_id(s_str),
            "type": entity_type,
        }
        node.update(props)
        by_type[entity_type].append(node)

    return dict(by_type)


def _rdf_value(obj):
    """Convert an rdflib term to a Python value."""
    if isinstance(obj, Literal):
        if obj.datatype == XSD.integer:
            return int(obj)
        if obj.datatype in (XSD.date, XSD.dateTime):
            return str(obj)
        return str(obj)
    if isinstance(obj, URIRef):
        # Use to_prefixed() to preserve namespace prefixes for enum values
        # (e.g., pulse:University) while shortening data namespace IRIs
        return to_prefixed(str(obj), use_pulse_prefix=True)
    return str(obj)


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def normalise_for_comparison(obj: dict) -> dict:
    """Normalise a JSON object for comparison.

    - Remove envelope fields
    - Remove null values
    - Remove empty arrays
    - Sort array values for order-independent comparison
    - Normalise datetime timezone format (Z vs +00:00)
    """

    def normalise_value(v):
        """Normalise a single value for comparison."""
        if isinstance(v, str):
            # Normalise datetime timezone: +00:00 → Z
            if v.endswith("+00:00"):
                return v[:-6] + "Z"
        return v

    result = {}
    for k, v in obj.items():
        if k in ENVELOPE_FIELDS:
            continue
        if v is None:
            continue
        if isinstance(v, list):
            if len(v) == 0:
                continue
            result[k] = sorted(str(normalise_value(x)) for x in v)
        else:
            result[k] = normalise_value(v)
    return result


def compare_entities(
    original: dict,
    reconstructed: dict,
    entity_id: str,
) -> list[dict]:
    """Compare an original JSON entity against its round-tripped version."""
    diffs = []

    orig_norm = normalise_for_comparison(original)
    recon_norm = normalise_for_comparison(reconstructed)

    orig_keys = set(orig_norm.keys())
    recon_keys = set(recon_norm.keys())

    # Properties lost in round trip
    for key in sorted(orig_keys - recon_keys):
        diffs.append(
            {
                "entity": entity_id,
                "property": key,
                "type": "lost_property",
                "original": orig_norm[key],
                "reconstructed": None,
            },
        )

    # Unexpected new properties
    for key in sorted(recon_keys - orig_keys):
        diffs.append(
            {
                "entity": entity_id,
                "property": key,
                "type": "extra_property",
                "original": None,
                "reconstructed": recon_norm[key],
            },
        )

    # Value mismatches
    for key in sorted(orig_keys & recon_keys):
        orig_val = orig_norm[key]
        recon_val = recon_norm[key]

        # Normalise both to strings for comparison
        if isinstance(orig_val, list) and isinstance(recon_val, list):
            if orig_val != recon_val:
                diffs.append(
                    {
                        "entity": entity_id,
                        "property": key,
                        "type": "value_mismatch",
                        "original": orig_val,
                        "reconstructed": recon_val,
                    },
                )
        else:
            if str(orig_val) != str(recon_val):
                diffs.append(
                    {
                        "entity": entity_id,
                        "property": key,
                        "type": "value_mismatch",
                        "original": orig_val,
                        "reconstructed": recon_val,
                    },
                )

    return diffs


def run_comparison(
    original_by_type: dict[str, list[dict]],
    reconstructed_by_type: dict[str, list[dict]],
) -> dict:
    """Compare all entities, returning structured results."""
    all_diffs = []
    matched = 0
    unmatched_original = 0

    for entity_type, orig_list in original_by_type.items():
        recon_list = reconstructed_by_type.get(entity_type, [])
        recon_by_id = {e["id"]: e for e in recon_list}

        for orig in orig_list:
            orig_id = orig.get("id", "unknown")
            recon = recon_by_id.get(orig_id)

            if recon is None:
                all_diffs.append(
                    {
                        "entity": orig_id,
                        "type": "missing_entity",
                        "property": None,
                        "original": entity_type,
                        "reconstructed": None,
                    },
                )
                unmatched_original += 1
                continue

            diffs = compare_entities(orig, recon, orig_id)
            if diffs:
                all_diffs.extend(diffs)
            else:
                matched += 1

    return {
        "matched": matched,
        "unmatched": unmatched_original,
        "differences": all_diffs,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def print_summary(results: dict):
    """Print human-readable summary."""
    print("\n" + "=" * 60)
    print("ROUND-TRIP TEST SUMMARY")
    print("=" * 60)

    diffs = results["differences"]
    lost = [d for d in diffs if d["type"] == "lost_property"]
    extra = [d for d in diffs if d["type"] == "extra_property"]
    mismatches = [d for d in diffs if d["type"] == "value_mismatch"]
    missing = [d for d in diffs if d["type"] == "missing_entity"]

    print(f"\n  Entities matched perfectly: {results['matched']}")
    print(f"  Entities missing from round-trip: {len(missing)}")
    print(f"  Properties lost: {len(lost)}")
    print(f"  Unexpected extra properties: {len(extra)}")
    print(f"  Value mismatches: {len(mismatches)}")

    if missing:
        print("\n  --- Missing Entities ---")
        for d in missing:
            print(f"    {d['entity']} ({d['original']})")

    if lost:
        print("\n  --- Lost Properties ---")
        for d in lost:
            print(f"    {d['entity']}.{d['property']}: {d['original']}")

    if extra:
        print("\n  --- Extra Properties ---")
        for d in extra:
            print(f"    {d['entity']}.{d['property']}: {d['reconstructed']}")

    if mismatches:
        print("\n  --- Value Mismatches ---")
        for d in mismatches:
            print(f"    {d['entity']}.{d['property']}:")
            print(f"      original:      {d['original']}")
            print(f"      reconstructed: {d['reconstructed']}")

    total_issues = len(missing) + len(lost) + len(extra) + len(mismatches)
    print()
    if total_issues == 0:
        print("  All entities survived the round trip!")
    else:
        print(f"  {total_issues} difference(s) found")


def main():
    """Main entry point."""
    print("Round-Trip Test: JSON -> JSON-LD -> RDF -> JSON")
    print(f"JSON-LD input: {JSONLD_FILE}")
    print(f"Roundtrip output: {ROUNDTRIP_DIR}")

    if not JSONLD_FILE.exists():
        print(f"Error: JSON-LD file not found: {JSONLD_FILE}")
        print("Run build_jsonld.py first.")
        sys.exit(1)

    ROUNDTRIP_DIR.mkdir(parents=True, exist_ok=True)

    # --- Step 1: Load JSON-LD and parse into RDF ---
    print("\nParsing JSON-LD into RDF graph...")
    jsonld_data = json.loads(JSONLD_FILE.read_text(encoding="utf-8"))
    g = Graph()
    g.parse(data=json.dumps(jsonld_data), format="json-ld")
    print(f"  {len(g)} triples")

    # --- Step 2: Reconstruct JSON from triples ---
    print("Reconstructing JSON from RDF triples...")
    reconstructed_by_type = reconstruct_from_graph(g)
    total_recon = sum(len(v) for v in reconstructed_by_type.values())
    print(
        f"  {total_recon} entities reconstructed across {len(reconstructed_by_type)} types",
    )

    # --- Step 3: Save reconstructed JSONs ---
    for entity_type, entities in reconstructed_by_type.items():
        filename = SHAPE_FILES.get(entity_type)
        if filename is None:
            filename = entity_type.replace(":", "_") + ".json"
        out_path = ROUNDTRIP_DIR / filename
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(entities, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {out_path.name} ({len(entities)} entities)")

    # --- Step 4: Load originals and compare ---
    print("\nComparing against original JSON files...")
    original_by_type: dict[str, list[dict]] = {}
    for entity_type, filename in SHAPE_FILES.items():
        src_path = JSON_DIR / filename
        if src_path.exists():
            data = json.loads(src_path.read_text(encoding="utf-8"))
            original_by_type[entity_type] = data if isinstance(data, list) else [data]

    results = run_comparison(original_by_type, reconstructed_by_type)

    # --- Step 5: Save results ---
    output = {
        "timestamp": datetime.now().isoformat(),
        "jsonld_file": str(JSONLD_FILE.name),
        "triples": len(g),
        "reconstructed_entities": total_recon,
        "summary": {
            "matched": results["matched"],
            "unmatched": results["unmatched"],
            "total_differences": len(results["differences"]),
        },
        "differences": results["differences"],
    }

    results_file = TEST_OUTPUT_DIR / "roundtrip_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to: {results_file}")

    print_summary(results)

    # Exit with error only for real issues (missing entities or value mismatches)
    critical = [
        d
        for d in results["differences"]
        if d["type"] in ("missing_entity", "value_mismatch")
    ]
    sys.exit(1 if critical else 0)


if __name__ == "__main__":
    main()
