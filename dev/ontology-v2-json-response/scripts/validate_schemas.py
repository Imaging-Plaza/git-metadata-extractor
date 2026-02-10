#!/usr/bin/env python3
"""
Validate JSON files in a-001 against both strict and agent JSON schemas.
Also runs expected-invalid regression fixtures to ensure key constraints do not
silently regress. Results are stored in a-001/test/ directory.

Usage:
    python scripts/validate_schemas.py

Requirements:
    pip install jsonschema
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft7Validator
except ImportError:
    print("Error: jsonschema not installed. Run: pip install jsonschema")
    exit(1)


# Paths (relative to ontology-v2-json-response directory)
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
JSON_DIR = BASE_DIR / "a-001"
STRICT_SCHEMA_DIR = JSON_DIR / "json-schema" / "strict"
AGENT_SCHEMA_DIR = JSON_DIR / "json-schema" / "agent"
TEST_OUTPUT_DIR = JSON_DIR / "test"
FIXTURES_DIR = JSON_DIR / "fixtures"

# Shape files to validate
SHAPE_FILES = [
    "pulse_PersonShape.json",
    "pulse_RepositoryShape.json",
    "pulse_OrganizationShape.json",
    "pulse_MembershipShape.json",
    "pulse_ContributionShape.json",
    "pulse_ArticleShape.json",
]

# Fixtures that are expected to FAIL validation.
NEGATIVE_EXPECTED_INVALID_CASES = [
    {
        "name": "person_invalid_null_identifiers_no_email",
        "fixture_file": "person_invalid_null_identifiers_no_email.json",
        "shape_name": "pulse_PersonShape",
        "schema_types": ["strict", "agent"],
    },
]


def load_json(filepath: Path) -> Any:
    """Load JSON file."""
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def validate_instance(
    instance: dict,
    schema: dict,
    validator: Draft7Validator,
) -> list[dict]:
    """Validate a single instance against a schema. Returns list of errors."""
    errors = []
    for error in validator.iter_errors(instance):
        errors.append(
            {
                "path": list(error.absolute_path),
                "message": error.message,
                "schema_path": list(error.schema_path),
                "validator": error.validator,
            },
        )
    return errors


def validate_file(json_file: Path, schema_file: Path) -> dict:
    """Validate a JSON file against a schema. Returns validation result."""
    result = {
        "json_file": str(json_file.name),
        "schema_file": str(schema_file.name),
        "valid_count": 0,
        "invalid_count": 0,
        "total_count": 0,
        "instances": [],
    }

    try:
        data = load_json(json_file)
        schema = load_json(schema_file)
    except FileNotFoundError as e:
        result["error"] = f"File not found: {e.filename}"
        return result
    except json.JSONDecodeError as e:
        result["error"] = f"JSON decode error: {e.msg}"
        return result

    validator = Draft7Validator(schema)

    # Handle both array and single object
    instances = data if isinstance(data, list) else [data]
    result["total_count"] = len(instances)

    for i, instance in enumerate(instances):
        instance_id = instance.get("id", f"index_{i}")
        errors = validate_instance(instance, schema, validator)

        instance_result = {
            "id": instance_id,
            "index": i,
            "valid": len(errors) == 0,
            "error_count": len(errors),
            "errors": errors,
        }

        if len(errors) == 0:
            result["valid_count"] += 1
        else:
            result["invalid_count"] += 1

        result["instances"].append(instance_result)

    return result


def validate_expected_invalid_case(
    *,
    case_name: str,
    fixture_file: Path,
    schema_file: Path,
    schema_type: str,
) -> dict:
    """Validate a fixture that is expected to be invalid."""
    result = {
        "name": case_name,
        "schema_type": schema_type,
        "fixture_file": str(fixture_file.relative_to(JSON_DIR)),
        "schema_file": str(schema_file.name),
        "expected_valid": False,
        "actual_valid": False,
        "passed": False,
        "error_count": 0,
        "errors": [],
    }

    try:
        data = load_json(fixture_file)
        schema = load_json(schema_file)
    except FileNotFoundError as e:
        result["error"] = f"File not found: {e.filename}"
        return result
    except json.JSONDecodeError as e:
        result["error"] = f"JSON decode error: {e.msg}"
        return result

    if not isinstance(data, dict):
        result["error"] = "Fixture must be a single JSON object"
        return result

    validator = Draft7Validator(schema)
    errors = validate_instance(data, schema, validator)
    actual_valid = len(errors) == 0

    result["actual_valid"] = actual_valid
    result["passed"] = not actual_valid
    result["error_count"] = len(errors)
    result["errors"] = errors
    return result


def run_negative_validation() -> dict:
    """Run expected-invalid fixture validation checks."""
    results = {
        "summary": {
            "total_cases": 0,
            "passed_cases": 0,
            "failed_cases": 0,
        },
        "cases": [],
    }

    schema_dirs = {
        "strict": STRICT_SCHEMA_DIR,
        "agent": AGENT_SCHEMA_DIR,
    }

    for case in NEGATIVE_EXPECTED_INVALID_CASES:
        fixture_path = FIXTURES_DIR / case["fixture_file"]
        for schema_type in case["schema_types"]:
            schema_dir = schema_dirs[schema_type]
            schema_path = schema_dir / f"{case['shape_name']}.schema.json"
            case_result = validate_expected_invalid_case(
                case_name=case["name"],
                fixture_file=fixture_path,
                schema_file=schema_path,
                schema_type=schema_type,
            )
            results["cases"].append(case_result)
            results["summary"]["total_cases"] += 1
            if case_result.get("passed"):
                results["summary"]["passed_cases"] += 1
            else:
                results["summary"]["failed_cases"] += 1

    return results


def run_validation() -> dict:
    """Run validation for all files against both schema types."""
    results = {
        "timestamp": datetime.now().isoformat(),
        "strict": {},
        "agent": {},
        "negative": {
            "summary": {
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
            },
            "cases": [],
        },
        "summary": {
            "strict": {
                "total_files": 0,
                "valid_files": 0,
                "total_instances": 0,
                "valid_instances": 0,
            },
            "agent": {
                "total_files": 0,
                "valid_files": 0,
                "total_instances": 0,
                "valid_instances": 0,
            },
            "negative": {
                "total_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
            },
        },
    }

    for shape_file in SHAPE_FILES:
        json_file = JSON_DIR / shape_file
        shape_name = shape_file.replace(".json", "")

        # Strict validation
        strict_schema = STRICT_SCHEMA_DIR / f"{shape_name}.schema.json"
        if json_file.exists() and strict_schema.exists():
            strict_result = validate_file(json_file, strict_schema)
            results["strict"][shape_name] = strict_result
            results["summary"]["strict"]["total_files"] += 1
            results["summary"]["strict"]["total_instances"] += strict_result.get(
                "total_count",
                0,
            )
            results["summary"]["strict"]["valid_instances"] += strict_result.get(
                "valid_count",
                0,
            )
            if (
                strict_result.get("invalid_count", 0) == 0
                and "error" not in strict_result
            ):
                results["summary"]["strict"]["valid_files"] += 1

        # Agent validation
        agent_schema = AGENT_SCHEMA_DIR / f"{shape_name}.schema.json"
        if json_file.exists() and agent_schema.exists():
            agent_result = validate_file(json_file, agent_schema)
            results["agent"][shape_name] = agent_result
            results["summary"]["agent"]["total_files"] += 1
            results["summary"]["agent"]["total_instances"] += agent_result.get(
                "total_count",
                0,
            )
            results["summary"]["agent"]["valid_instances"] += agent_result.get(
                "valid_count",
                0,
            )
            if (
                agent_result.get("invalid_count", 0) == 0
                and "error" not in agent_result
            ):
                results["summary"]["agent"]["valid_files"] += 1

    negative_results = run_negative_validation()
    results["negative"] = negative_results
    results["summary"]["negative"] = negative_results["summary"]

    return results


def print_summary(results: dict):
    """Print a summary of validation results."""
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    for schema_type in ["strict", "agent"]:
        summary = results["summary"][schema_type]
        print(f"\n{schema_type.upper()} SCHEMAS:")
        print(f"  Files: {summary['valid_files']}/{summary['total_files']} valid")
        print(
            f"  Instances: {summary['valid_instances']}/{summary['total_instances']} valid",
        )

        # Show details for invalid files
        for shape_name, result in results[schema_type].items():
            if result.get("invalid_count", 0) > 0 or "error" in result:
                print(f"\n  ❌ {shape_name}:")
                if "error" in result:
                    print(f"     Error: {result['error']}")
                else:
                    for inst in result["instances"]:
                        if not inst["valid"]:
                            print(
                                f"     - {inst['id']}: {inst['error_count']} error(s)",
                            )
                            for err in inst["errors"][:3]:  # Show first 3 errors
                                path = ".".join(str(p) for p in err["path"]) or "(root)"
                                print(f"       [{path}] {err['message']}")
                            if len(inst["errors"]) > 3:
                                print(
                                    f"       ... and {len(inst['errors']) - 3} more errors",
                                )
            else:
                print(
                    f"\n  ✓ {shape_name}: {result.get('valid_count', 0)} instances valid",
                )

    negative_results = results.get("negative", {})
    negative_summary = negative_results.get("summary", {})
    if negative_summary:
        print("\nNEGATIVE REGRESSION CASES:")
        print(
            "  Cases: "
            f"{negative_summary.get('passed_cases', 0)}/"
            f"{negative_summary.get('total_cases', 0)} passed",
        )

        for case in negative_results.get("cases", []):
            status = "✓" if case.get("passed") else "❌"
            print(f"\n  {status} {case['name']} ({case['schema_type']})")
            if "error" in case:
                print(f"     Error: {case['error']}")
                continue

            if case.get("passed"):
                print("     Expected invalid and validation failed as intended")
            else:
                print("     Expected invalid but fixture validated successfully")
                for err in case.get("errors", [])[:3]:
                    path = ".".join(str(p) for p in err["path"]) or "(root)"
                    print(f"       [{path}] {err['message']}")
                if len(case.get("errors", [])) > 3:
                    print(
                        f"       ... and {len(case['errors']) - 3} more errors",
                    )


def main():
    """Main entry point."""
    print(f"Validating JSON files in: {JSON_DIR}")
    print(f"Strict schemas: {STRICT_SCHEMA_DIR}")
    print(f"Agent schemas: {AGENT_SCHEMA_DIR}")
    print(f"Output directory: {TEST_OUTPUT_DIR}")

    # Create output directory
    TEST_OUTPUT_DIR.mkdir(exist_ok=True)

    # Run validation
    results = run_validation()

    # Save full results
    full_results_file = TEST_OUTPUT_DIR / "validation_results.json"
    with open(full_results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to: {full_results_file}")

    # Save separate reports for strict and agent
    for schema_type in ["strict", "agent"]:
        report_file = TEST_OUTPUT_DIR / f"validation_{schema_type}.json"
        report = {
            "timestamp": results["timestamp"],
            "schema_type": schema_type,
            "summary": results["summary"][schema_type],
            "results": results[schema_type],
        }
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"{schema_type.capitalize()} report saved to: {report_file}")

    negative_report_file = TEST_OUTPUT_DIR / "validation_negative.json"
    negative_report = {
        "timestamp": results["timestamp"],
        "schema_type": "negative",
        "summary": results["negative"]["summary"],
        "results": results["negative"]["cases"],
    }
    with open(negative_report_file, "w", encoding="utf-8") as f:
        json.dump(negative_report, f, indent=2)
    print(f"Negative report saved to: {negative_report_file}")

    # Print summary
    print_summary(results)

    # Exit with error code if validation failed
    total_invalid = (
        results["summary"]["strict"]["total_instances"]
        - results["summary"]["strict"]["valid_instances"]
        + results["summary"]["agent"]["total_instances"]
        - results["summary"]["agent"]["valid_instances"]
        + results["summary"]["negative"]["failed_cases"]
    )
    if total_invalid > 0:
        print(f"\n⚠️  {total_invalid} validation error(s) found")
        exit(1)
    else:
        print("\n✅ All validations passed!")
        exit(0)


if __name__ == "__main__":
    main()
