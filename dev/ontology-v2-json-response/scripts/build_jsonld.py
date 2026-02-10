#!/usr/bin/env python3
"""
Build JSON-LD from shape JSON files and validate internal consistency.
Checks that all cross-references between shapes are valid.

Usage:
    python scripts/build_jsonld.py

Output:
    - a-001/test/jsonld_output.json - Combined JSON-LD document
    - a-001/test/consistency_results.json - Cross-reference validation results
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# Paths
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
JSON_DIR = BASE_DIR / "a-001"
TEST_OUTPUT_DIR = JSON_DIR / "test"

# Shape files
SHAPE_FILES = {
    "Person": "pulse_PersonShape.json",
    "Repository": "pulse_RepositoryShape.json",
    "Organization": "pulse_OrganizationShape.json",
    "Membership": "pulse_MembershipShape.json",
    "Contribution": "pulse_ContributionShape.json",
    "Article": "pulse_ArticleShape.json",
}

# JSON-LD Context
JSONLD_CONTEXT = {
    "@vocab": "https://open-pulse.epfl.ch/ontology#",
    "schema": "http://schema.org/",
    "org": "http://www.w3.org/ns/org#",
    "time": "http://www.w3.org/2006/time#",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "wd": "http://www.wikidata.org/entity/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    # Type mappings
    "Person": "schema:Person",
    "Repository": "schema:SoftwareSourceCode",
    "Organization": "org:Organization",
    "Membership": "org:Membership",
    "Contribution": "pulse:Contribution",
    "Article": "schema:ScholarlyArticle",
    # Property mappings
    "name": "schema:name",
    "email": "schema:email",
    "url": "schema:url",
    "author": {"@id": "schema:author", "@type": "@id"},
    "dateCreated": {"@id": "schema:dateCreated", "@type": "xsd:dateTime"},
    "datePublished": {"@id": "schema:datePublished", "@type": "xsd:date"},
    "license": {"@id": "schema:license", "@type": "@id"},
    "citation": {"@id": "schema:citation", "@type": "@id"},
    "programmingLanguage": "schema:programmingLanguage",
    "sourceOrganization": {"@id": "schema:sourceOrganization", "@type": "@id"},
    "identifier": "schema:identifier",
    # Org ontology
    "organization": {"@id": "org:organization", "@type": "@id"},
    "hasMembership": {"@id": "org:hasMembership", "@type": "@id"},
    "hasUnit": {"@id": "org:hasUnit", "@type": "@id"},
    "unitOf": {"@id": "org:unitOf", "@type": "@id"},
    "role": "org:role",
    # Time ontology
    "hasBeginning": {"@id": "time:hasBeginning", "@type": "xsd:date"},
    "hasEnd": {"@id": "time:hasEnd", "@type": "xsd:date"},
    # Pulse ontology
    "githubUsername": "pulse:githubUsername",
    "githubOrganizationHandle": "pulse:githubOrganizationHandle",
    "githubRepositoryHandle": "pulse:githubRepositoryHandle",
    "orcidIdentifier": "pulse:orcidIdentifier",
    "infosciencePersonIdentifier": "pulse:infosciencePersonIdentifier",
    "infoscienceArticleIdentifier": "pulse:infoscienceArticleIdentifier",
    "repositoryType": {"@id": "pulse:repositoryType", "@type": "@id"},
    "OrganizationType": {"@id": "pulse:OrganizationType", "@type": "@id"},
    "discipline": {"@id": "pulse:discipline", "@type": "@id"},
    "owns": {"@id": "pulse:owns", "@type": "@id"},
    "ownedBy": {"@id": "pulse:ownedBy", "@type": "@id"},
    "isForkOf": {"@id": "pulse:isForkOf", "@type": "@id"},
    "hasContribution": {"@id": "pulse:hasContribution", "@type": "@id"},
    "contributionTo": {"@id": "pulse:contributionTo", "@type": "@id"},
    "contributionCount": {"@id": "pulse:contributionCount", "@type": "xsd:integer"},
    "firstContributionDate": {
        "@id": "pulse:firstContributionDate",
        "@type": "xsd:dateTime",
    },
    "lastContributionDate": {
        "@id": "pulse:lastContributionDate",
        "@type": "xsd:dateTime",
    },
    "githubRepoStars": {"@id": "pulse:githubRepoStars", "@type": "xsd:integer"},
    "githubRepoForks": {"@id": "pulse:githubRepoForks", "@type": "xsd:integer"},
    "githubOrgFollowers": {"@id": "pulse:githubOrgFollowers", "@type": "xsd:integer"},
    # Prefixed property mappings (for source JSON that uses pulse: prefix)
    "pulse:repositoryType": {"@id": "pulse:repositoryType", "@type": "@id"},
    "pulse:OrganizationType": {"@id": "pulse:OrganizationType", "@type": "@id"},
    "pulse:discipline": {"@id": "pulse:discipline", "@type": "@id"},
    "pulse:owns": {"@id": "pulse:owns", "@type": "@id"},
    "pulse:ownedBy": {"@id": "pulse:ownedBy", "@type": "@id"},
    "pulse:isForkOf": {"@id": "pulse:isForkOf", "@type": "@id"},
    "pulse:hasContribution": {"@id": "pulse:hasContribution", "@type": "@id"},
    "pulse:contributionTo": {"@id": "pulse:contributionTo", "@type": "@id"},
    "pulse:contributionCount": {
        "@id": "pulse:contributionCount",
        "@type": "xsd:integer",
    },
    "pulse:firstContributionDate": {
        "@id": "pulse:firstContributionDate",
        "@type": "xsd:dateTime",
    },
    "pulse:lastContributionDate": {
        "@id": "pulse:lastContributionDate",
        "@type": "xsd:dateTime",
    },
    "pulse:githubRepoStars": {"@id": "pulse:githubRepoStars", "@type": "xsd:integer"},
    "pulse:githubRepoForks": {"@id": "pulse:githubRepoForks", "@type": "xsd:integer"},
    "pulse:githubOrgFollowers": {
        "@id": "pulse:githubOrgFollowers",
        "@type": "xsd:integer",
    },
    "pulse:githubUsername": "pulse:githubUsername",
    "pulse:githubOrganizationHandle": "pulse:githubOrganizationHandle",
    "pulse:githubRepositoryHandle": "pulse:githubRepositoryHandle",
    "pulse:orcidIdentifier": "pulse:orcidIdentifier",
    "pulse:infosciencePersonIdentifier": "pulse:infosciencePersonIdentifier",
    "pulse:infoscienceArticleIdentifier": "pulse:infoscienceArticleIdentifier",
    "pulse:infoscienceOrganizationIdentifier": "pulse:infoscienceOrganizationIdentifier",
    "pulse:ror": {"@id": "pulse:ror", "@type": "@id"},
    "pulse:orcid": {"@id": "pulse:orcid", "@type": "@id"},
    # Schema.org prefixed mappings
    "schema:name": "schema:name",
    "schema:email": "schema:email",
    "schema:url": {"@id": "schema:url", "@type": "@id"},
    "schema:author": {"@id": "schema:author", "@type": "@id"},
    "schema:identifier": "schema:identifier",
    "schema:dateCreated": {"@id": "schema:dateCreated", "@type": "xsd:dateTime"},
    "schema:datePublished": {"@id": "schema:datePublished", "@type": "xsd:date"},
    "schema:dateModified": {"@id": "schema:dateModified", "@type": "xsd:dateTime"},
    "schema:license": {"@id": "schema:license", "@type": "@id"},
    "schema:citation": {"@id": "schema:citation", "@type": "@id"},
    "schema:sourceOrganization": {"@id": "schema:sourceOrganization", "@type": "@id"},
    "schema:programmingLanguage": "schema:programmingLanguage",
    # Org ontology prefixed mappings
    "org:organization": {"@id": "org:organization", "@type": "@id"},
    "org:hasMembership": {"@id": "org:hasMembership", "@type": "@id"},
    "org:hasUnit": {"@id": "org:hasUnit", "@type": "@id"},
    "org:unitOf": {"@id": "org:unitOf", "@type": "@id"},
    "org:role": "org:role",
    # Time ontology prefixed mappings
    "time:hasBeginning": {"@id": "time:hasBeginning", "@type": "xsd:date"},
    "time:hasEnd": {"@id": "time:hasEnd", "@type": "xsd:date"},
}

# Cross-reference definitions: (source_shape, field) -> target_shape
CROSS_REFERENCES = {
    ("Person", "org:hasMembership"): "Membership",
    ("Person", "pulse:hasContribution"): "Contribution",
    ("Person", "pulse:owns"): "Repository",
    ("Repository", "schema:author"): "Person",
    ("Repository", "pulse:ownedBy"): ["Person", "Organization"],  # Can reference either
    ("Repository", "pulse:isForkOf"): "Repository",
    ("Organization", "org:hasUnit"): "Organization",
    ("Organization", "org:unitOf"): "Organization",
    ("Organization", "pulse:owns"): "Repository",
    ("Membership", "org:organization"): "Organization",
    ("Contribution", "pulse:contributionTo"): "Repository",
    ("Contribution", "schema:author"): "Person",
    ("Article", "schema:author"): "Person",
    ("Article", "schema:sourceOrganization"): "Organization",
}

# ID hierarchy for each shape (order of priority)
# Used to validate that cross-references use hierarchical IDs, not internal UUIDs
ID_HIERARCHIES = {
    "Person": [
        "pulse:orcid",
        "pulse:infosciencePersonIdentifier",
        "pulse:githubUsername",
        "uuid",
    ],
    "Repository": ["pulse:githubRepositoryHandle", "schema:identifier", "uuid"],
    "Organization": [
        "pulse:ror",
        "pulse:infoscienceOrganizationIdentifier",
        "pulse:githubOrganizationHandle",
        "uuid",
    ],
    "Article": ["schema:identifier", "pulse:infoscienceArticleIdentifier", "uuid"],
    "Membership": ["pulse:composite", "uuid"],
    "Contribution": ["pulse:composite", "uuid"],
}

# Regex patterns for common ID formats
import re

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
ROR_PATTERN = re.compile(r"^https://ror\.org/[0-9a-z]{9}$")
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+$")


def is_uuid_format(value: str) -> bool:
    """Check if a string looks like a UUID."""
    return bool(UUID_PATTERN.match(value))


def is_hierarchical_id(value: str, target_shape: str) -> bool:
    """Check if a reference value looks like a hierarchical ID (not a UUID fallback)."""
    if not value:
        return False

    # If it's a UUID, check if target shape has higher priority IDs available
    if is_uuid_format(value):
        # UUID should only be used if no higher-priority ID is available
        # For cross-references, we expect hierarchical IDs when available
        return False

    # Known hierarchical ID patterns
    if ORCID_PATTERN.match(value):
        return True
    if ROR_PATTERN.match(value):
        return True
    if DOI_PATTERN.match(value):
        return True
    if "/" in value:  # GitHub handles like owner/repo or org names
        return True
    if value.startswith("http"):  # URLs like ROR
        return True

    # Non-UUID, non-URL values: GitHub usernames, composite IDs, etc.
    # Any non-empty, non-UUID string is treated as a hierarchical ID.
    return len(value) > 0


def load_json(filepath: Path) -> Any:
    """Load JSON file."""
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def load_all_shapes() -> dict[str, list[dict]]:
    """Load all shape JSON files."""
    shapes = {}
    for shape_name, filename in SHAPE_FILES.items():
        filepath = JSON_DIR / filename
        if filepath.exists():
            data = load_json(filepath)
            shapes[shape_name] = data if isinstance(data, list) else [data]
        else:
            shapes[shape_name] = []
    return shapes


def build_id_index(shapes: dict[str, list[dict]]) -> dict[str, dict[str, set]]:
    """
    Build an index of all IDs per shape type.
    Returns: {shape_name: {id: set_of_all_identifiers}}
    """
    index = {}
    for shape_name, instances in shapes.items():
        index[shape_name] = {}
        for instance in instances:
            primary_id = instance.get("id")
            identifiers = instance.get("identifiers", {})

            # Collect all possible IDs for this instance
            all_ids = {primary_id}
            for key, value in identifiers.items():
                if value is not None:
                    all_ids.add(value)

            # Map primary ID to all identifiers
            index[shape_name][primary_id] = all_ids

    return index


def find_id_in_shape(
    ref_id: str,
    shape_name: str,
    id_index: dict,
) -> tuple[bool, str | None]:
    """
    Check if a reference ID exists in the target shape.
    Returns (found, matched_primary_id)
    """
    if shape_name not in id_index:
        return False, None

    for primary_id, all_ids in id_index[shape_name].items():
        if ref_id in all_ids or ref_id == primary_id:
            return True, primary_id

    return False, None


def validate_cross_references(shapes: dict[str, list[dict]], id_index: dict) -> dict:
    """Validate all cross-references between shapes."""
    results = {
        "valid_references": 0,
        "invalid_references": 0,
        "errors": [],
        "warnings": [],
        "details": defaultdict(list),
    }

    for shape_name, instances in shapes.items():
        for instance in instances:
            instance_id = instance.get("id", "unknown")

            # Check each cross-reference field
            for (src_shape, field), target_shapes in CROSS_REFERENCES.items():
                if src_shape != shape_name:
                    continue

                # Handle field name variations (with and without prefix)
                field_value = instance.get(field)
                if field_value is None:
                    # Try without prefix
                    short_field = field.split(":")[-1] if ":" in field else field
                    field_value = instance.get(short_field)

                if field_value is None:
                    continue

                # Normalize to list
                if not isinstance(target_shapes, list):
                    target_shapes = [target_shapes]

                # Handle single value or array
                refs = field_value if isinstance(field_value, list) else [field_value]

                for ref in refs:
                    if ref is None:
                        continue

                    # Check if reference exists in any of the target shapes
                    found = False
                    matched_shape = None
                    matched_id = None

                    for target_shape in target_shapes:
                        found, matched_id = find_id_in_shape(
                            ref,
                            target_shape,
                            id_index,
                        )
                        if found:
                            matched_shape = target_shape
                            break

                    # Check if reference uses UUID instead of hierarchical ID
                    uses_uuid = is_uuid_format(ref) if ref else False
                    id_format_warning = None
                    if uses_uuid and found:
                        # Check if the target entity has a higher-priority ID available
                        target_instance = None
                        for inst in shapes.get(matched_shape, []):
                            if inst.get("id") == matched_id:
                                target_instance = inst
                                break

                        if target_instance:
                            target_primary_id = target_instance.get("id")
                            # If target has a hierarchical ID but we're using UUID, warn
                            if target_primary_id and not is_uuid_format(
                                target_primary_id,
                            ):
                                id_format_warning = {
                                    "type": "uuid_instead_of_hierarchical_id",
                                    "source_shape": shape_name,
                                    "source_id": instance_id,
                                    "field": field,
                                    "reference_used": ref,
                                    "should_use": target_primary_id,
                                    "target_shape": matched_shape,
                                    "message": f"Cross-reference uses UUID '{ref}' but target has hierarchical ID '{target_primary_id}'",
                                }
                                results["warnings"].append(id_format_warning)

                    if found:
                        results["valid_references"] += 1
                        detail_entry = {
                            "source_id": instance_id,
                            "field": field,
                            "reference": ref,
                            "target_shape": matched_shape,
                            "resolved_id": matched_id,
                            "status": "valid",
                        }
                        if id_format_warning:
                            detail_entry["warning"] = id_format_warning["message"]
                        results["details"][shape_name].append(detail_entry)
                    else:
                        results["invalid_references"] += 1
                        error = {
                            "source_shape": shape_name,
                            "source_id": instance_id,
                            "field": field,
                            "reference": ref,
                            "expected_target": target_shapes,
                            "message": f"Reference '{ref}' not found in {target_shapes}",
                        }
                        results["errors"].append(error)
                        results["details"][shape_name].append(
                            {
                                "source_id": instance_id,
                                "field": field,
                                "reference": ref,
                                "target_shape": target_shapes,
                                "status": "invalid",
                                "error": error["message"],
                            },
                        )

    return results


def check_bidirectional_consistency(
    shapes: dict[str, list[dict]],
    id_index: dict,
) -> list[dict]:
    """Check that bidirectional references are consistent."""
    warnings = []

    # Build reverse ownership map: repo_id -> list of (owner_type, owner_id, owner_all_ids)
    repo_owners = defaultdict(list)

    for person in shapes.get("Person", []):
        person_id = person.get("id")
        person_ids = id_index["Person"].get(person_id, {person_id})
        for repo_id in person.get("pulse:owns", []):
            repo_owners[repo_id].append(("Person", person_id, person_ids))

    for org in shapes.get("Organization", []):
        org_id = org.get("id")
        org_ids = id_index["Organization"].get(org_id, {org_id})
        for repo_id in org.get("pulse:owns", []):
            repo_owners[repo_id].append(("Organization", org_id, org_ids))

    # Check each repository's ownedBy references one of its owners
    for repo in shapes.get("Repository", []):
        repo_id = repo.get("id")
        owned_by = repo.get("pulse:ownedBy")

        if owned_by is None:
            continue

        owners = repo_owners.get(repo_id, [])
        if not owners:
            warnings.append(
                {
                    "type": "orphan_ownership",
                    "message": f"Repository '{repo_id}' has ownedBy='{owned_by}', but no Person or Organization claims to own it",
                    "repo_id": repo_id,
                    "repo_owned_by": owned_by,
                },
            )
            continue

        # Check if ownedBy matches any owner
        matched = False
        for owner_type, owner_id, owner_ids in owners:
            if owned_by == owner_id or owned_by in owner_ids:
                matched = True
                break

        if not matched:
            # Multiple owners claim the repo but ownedBy doesn't match any
            owner_list = [(t, i) for t, i, _ in owners]
            warnings.append(
                {
                    "type": "bidirectional_mismatch",
                    "message": f"Repository '{repo_id}' has ownedBy='{owned_by}', but claimed owners are {owner_list}",
                    "repo_id": repo_id,
                    "repo_owned_by": owned_by,
                    "claimed_owners": owner_list,
                },
            )
        elif len(owners) > 1:
            # Multiple owners claim the repo - may be intentional but worth noting
            owner_list = [(t, i) for t, i, _ in owners]
            warnings.append(
                {
                    "type": "multiple_owners",
                    "message": f"Repository '{repo_id}' is claimed by multiple owners: {owner_list} (ownedBy='{owned_by}')",
                    "repo_id": repo_id,
                    "repo_owned_by": owned_by,
                    "claimed_owners": owner_list,
                },
            )

    # Check Person.org:hasMembership references exist
    for person in shapes.get("Person", []):
        person_id = person.get("id")
        memberships = person.get("org:hasMembership", [])

        for membership_id in memberships:
            found = False
            for membership in shapes.get("Membership", []):
                if membership.get("id") == membership_id:
                    found = True
                    # Check membership's composite ID contains person's ID
                    composite = membership.get("identifiers", {}).get(
                        "pulse:composite",
                        "",
                    )
                    if person_id not in composite:
                        person_ids = id_index["Person"].get(person_id, set())
                        if not any(pid in composite for pid in person_ids):
                            warnings.append(
                                {
                                    "type": "membership_person_mismatch",
                                    "message": f"Person '{person_id}' references Membership '{membership_id}', but composite ID doesn't contain person's ID",
                                    "person_id": person_id,
                                    "membership_id": membership_id,
                                    "composite": composite,
                                },
                            )
                    break

    return warnings


def build_jsonld(shapes: dict[str, list[dict]]) -> dict:
    """Build a JSON-LD document from all shapes."""
    jsonld = {
        "@context": JSONLD_CONTEXT,
        "@graph": [],
    }

    for shape_name, instances in shapes.items():
        for instance in instances:
            # Create JSON-LD node - use original ID directly to match cross-references
            node = {
                "@id": instance.get("id", "unknown"),
                "@type": instance.get("type", shape_name),
            }

            # Copy relevant properties (skip metadata/helper fields)
            # These are internal properties for building/validating, not semantic data:
            # - shacl: SHACL shape reference for validation
            # - identifiers: all possible IDs for cross-reference lookups
            # - idSource: indicates which identifier was used as primary id
            skip_fields = {"id", "type", "shacl", "identifiers", "idSource"}
            for key, value in instance.items():
                if key not in skip_fields and value is not None:
                    node[key] = value

            jsonld["@graph"].append(node)

    return jsonld


def print_summary(cross_ref_results: dict, bidirectional_warnings: list):
    """Print validation summary."""
    print("\n" + "=" * 60)
    print("CONSISTENCY VALIDATION SUMMARY")
    print("=" * 60)

    print("\nCross-References:")
    print(f"  ✓ Valid: {cross_ref_results['valid_references']}")
    print(f"  ✗ Invalid: {cross_ref_results['invalid_references']}")

    if cross_ref_results["errors"]:
        print("\nCross-Reference Errors:")
        for err in cross_ref_results["errors"]:
            print(f"  ❌ [{err['source_shape']}] {err['source_id']}")
            print(f"     Field: {err['field']}")
            print(f"     Reference: {err['reference']}")
            print(f"     Expected in: {err['expected_target']}")

    # Show ID format warnings (UUID used instead of hierarchical ID)
    id_format_warnings = [
        w
        for w in cross_ref_results.get("warnings", [])
        if w.get("type") == "uuid_instead_of_hierarchical_id"
    ]
    if id_format_warnings:
        print(f"\n⚠️  ID Format Warnings: {len(id_format_warnings)}")
        print("   Cross-references should use hierarchical IDs, not UUIDs:")
        for warn in id_format_warnings[:5]:  # Show first 5
            print(f"  ⚠️  [{warn['source_shape']}] {warn['source_id']}")
            print(f"     Field: {warn['field']}")
            print(f"     Used: {warn['reference_used']}")
            print(f"     Should use: {warn['should_use']}")
        if len(id_format_warnings) > 5:
            print(f"     ... and {len(id_format_warnings) - 5} more")

    if bidirectional_warnings:
        print(f"\nBidirectional Consistency Warnings: {len(bidirectional_warnings)}")
        for warn in bidirectional_warnings:
            print(f"  ⚠️  {warn['message']}")
    else:
        print("\n✓ Bidirectional references are consistent")

    total_issues = (
        cross_ref_results["invalid_references"]
        + len(bidirectional_warnings)
        + len(id_format_warnings)
    )
    if total_issues == 0:
        print("\n✅ All consistency checks passed!")
    else:
        print(f"\n⚠️  {total_issues} issue(s) found")


def main():
    """Main entry point."""
    print(f"Loading JSON files from: {JSON_DIR}")
    print(f"Output directory: {TEST_OUTPUT_DIR}")

    # Create output directory
    TEST_OUTPUT_DIR.mkdir(exist_ok=True)

    # Load all shapes
    shapes = load_all_shapes()
    total_instances = sum(len(instances) for instances in shapes.values())
    print(f"Loaded {total_instances} instances across {len(shapes)} shapes")

    # Build ID index
    id_index = build_id_index(shapes)

    # Validate cross-references
    cross_ref_results = validate_cross_references(shapes, id_index)

    # Check bidirectional consistency
    bidirectional_warnings = check_bidirectional_consistency(shapes, id_index)
    cross_ref_results["bidirectional_warnings"] = bidirectional_warnings

    # Build JSON-LD
    jsonld = build_jsonld(shapes)

    # Save JSON-LD output
    jsonld_file = TEST_OUTPUT_DIR / "jsonld_output.json"
    with open(jsonld_file, "w", encoding="utf-8") as f:
        json.dump(jsonld, f, indent=2)
    print(f"\nJSON-LD saved to: {jsonld_file}")

    # Extract ID format warnings
    id_format_warnings = [
        w
        for w in cross_ref_results.get("warnings", [])
        if w.get("type") == "uuid_instead_of_hierarchical_id"
    ]

    # Save consistency results
    consistency_results = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_instances": total_instances,
            "valid_references": cross_ref_results["valid_references"],
            "invalid_references": cross_ref_results["invalid_references"],
            "id_format_warnings": len(id_format_warnings),
            "bidirectional_warnings": len(bidirectional_warnings),
        },
        "cross_reference_errors": cross_ref_results["errors"],
        "id_format_warnings": id_format_warnings,
        "bidirectional_warnings": bidirectional_warnings,
        "details": dict(cross_ref_results["details"]),
    }

    consistency_file = TEST_OUTPUT_DIR / "consistency_results.json"
    with open(consistency_file, "w", encoding="utf-8") as f:
        json.dump(consistency_results, f, indent=2)
    print(f"Consistency results saved to: {consistency_file}")

    # Print summary
    print_summary(cross_ref_results, bidirectional_warnings)

    # Exit with error code if issues found
    total_issues = (
        cross_ref_results["invalid_references"]
        + len(bidirectional_warnings)
        + len(id_format_warnings)
    )
    exit(1 if total_issues > 0 else 0)


if __name__ == "__main__":
    main()
