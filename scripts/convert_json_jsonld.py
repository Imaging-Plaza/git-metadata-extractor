#!/usr/bin/env python3
"""
CLI tool for converting between JSON and JSON-LD formats.

Usage:
    # Convert Pydantic JSON to JSON-LD
    python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld

    # Convert JSON-LD to Pydantic JSON
    python scripts/convert_json_jsonld.py to-json input.jsonld output.json

    # With custom base URL
    python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld --base-url https://github.com/user/repo
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from data_models.conversion import (
    convert_jsonld_to_pydantic,
    convert_pydantic_to_jsonld,
)
from data_models.organization import GitHubOrganization
from data_models.repository import SoftwareSourceCode
from data_models.user import GitHubUser


def convert_to_jsonld(
    input_file: Path,
    output_file: Path,
    base_url: Optional[str] = None,
):
    """Convert Pydantic JSON to JSON-LD format."""
    print(f"📖 Reading Pydantic JSON from: {input_file}")

    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)

    print("🔄 Converting to Pydantic model...")

    # Detect type and validate
    model_obj = None
    model_type = None

    # Check if it's from the API output format (has "output" wrapper)
    if "output" in data and "type" in data:
        api_type = data.get("type")
        inner_data = data.get("output", {})

        if api_type == "organization":
            try:
                model_obj = GitHubOrganization(**inner_data)
                model_type = "GitHubOrganization"
                print("✅ Successfully validated as GitHubOrganization")
            except Exception as e:
                print(f"❌ Error validating as GitHubOrganization: {e}")
                sys.exit(1)
        elif api_type == "user":
            try:
                model_obj = GitHubUser(**inner_data)
                model_type = "GitHubUser"
                print("✅ Successfully validated as GitHubUser")
            except Exception as e:
                print(f"❌ Error validating as GitHubUser: {e}")
                sys.exit(1)
        elif api_type == "repository":
            try:
                model_obj = SoftwareSourceCode(**inner_data)
                model_type = "SoftwareSourceCode"
                print("✅ Successfully validated as SoftwareSourceCode")
            except Exception as e:
                print(f"❌ Error validating as SoftwareSourceCode: {e}")
                sys.exit(1)
        else:
            print(f"❌ Unknown API type: {api_type}")
            sys.exit(1)
    else:
        # Try to detect model type from data structure
        # Try SoftwareSourceCode first (has repositoryType)
        if "repositoryType" in data or "codeRepository" in data:
            try:
                model_obj = SoftwareSourceCode(**data)
                model_type = "SoftwareSourceCode"
                print("✅ Successfully validated as SoftwareSourceCode")
            except Exception as e:
                print(f"❌ Error validating as SoftwareSourceCode: {e}")
                sys.exit(1)
        # Try GitHubOrganization (has organizationType or githubOrganizationMetadata)
        elif "organizationType" in data or "githubOrganizationMetadata" in data:
            try:
                model_obj = GitHubOrganization(**data)
                model_type = "GitHubOrganization"
                print("✅ Successfully validated as GitHubOrganization")
            except Exception as e:
                print(f"❌ Error validating as GitHubOrganization: {e}")
                sys.exit(1)
        # Try GitHubUser (has githubHandle or githubUserMetadata)
        elif "githubHandle" in data or "githubUserMetadata" in data:
            try:
                model_obj = GitHubUser(**data)
                model_type = "GitHubUser"
                print("✅ Successfully validated as GitHubUser")
            except Exception as e:
                print(f"❌ Error validating as GitHubUser: {e}")
                sys.exit(1)
        else:
            print(
                "❌ Could not detect model type. Expected SoftwareSourceCode, GitHubOrganization, or GitHubUser",
            )
            sys.exit(1)

    print("🔄 Converting to JSON-LD...")

    # Convert to JSON-LD
    jsonld = convert_pydantic_to_jsonld(model_obj, base_url=base_url)

    print(f"💾 Writing JSON-LD to: {output_file}")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(jsonld, f, indent=2, ensure_ascii=False)

    print("✅ Conversion complete!")
    print("\n📊 Summary:")
    print(f"   - Type:   {model_type}")
    print(f"   - Input:  {input_file} ({input_file.stat().st_size:,} bytes)")
    print(f"   - Output: {output_file} ({output_file.stat().st_size:,} bytes)")
    if base_url:
        print(f"   - Base URL: {base_url}")


def convert_to_json(input_file: Path, output_file: Path):
    """Convert JSON-LD to Pydantic JSON format."""
    print(f"📖 Reading JSON-LD from: {input_file}")

    with open(input_file, encoding="utf-8") as f:
        jsonld_data = json.load(f)

    print("🔄 Converting to Pydantic model...")

    # Extract graph if present
    graph = jsonld_data.get("@graph", [jsonld_data])

    # Detect the type from @type in the graph
    model_obj = None
    model_type = None

    if isinstance(graph, list) and len(graph) > 0:
        first_entity = graph[0]
        entity_type = first_entity.get("@type", "")

        # Detect based on @type
        if entity_type in ["schema:SoftwareSourceCode"]:
            # Try to convert as repository
            try:
                model_obj = convert_jsonld_to_pydantic(graph)
                if model_obj:
                    model_type = "SoftwareSourceCode"
                    print("✅ Successfully converted to SoftwareSourceCode")
            except Exception as e:
                print(f"❌ Error converting as SoftwareSourceCode: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif entity_type in ["schema:GitHubOrganization", "schema:Organization"]:
            # For now, organizations need manual reconstruction since convert_jsonld_to_pydantic
            # only handles SoftwareSourceCode. Create GitHubOrganization from the data
            print("⚠️  Note: Organization conversion from JSON-LD is simplified")
            try:
                # Extract basic fields - this is a simplified conversion
                org_data = {
                    "name": first_entity.get("schema:name"),
                    "organizationType": first_entity.get("schema:additionalType"),
                    "description": first_entity.get("schema:description"),
                }
                # TODO: Implement full reverse mapping for organizations
                model_obj = org_data  # Return as dict for now
                model_type = "GitHubOrganization"
                print("✅ Extracted organization data (simplified)")
            except Exception as e:
                print(f"❌ Error converting organization: {e}")
                sys.exit(1)
        elif entity_type in ["schema:Person"]:
            # Check if it's a GitHubUser (has username) or generic Person
            if "schema:username" in first_entity or "pulse:metadata" in first_entity:
                print("⚠️  Note: User conversion from JSON-LD is simplified")
                try:
                    user_data = {
                        "name": first_entity.get("schema:name"),
                        "githubHandle": first_entity.get("schema:username"),
                    }
                    # TODO: Implement full reverse mapping for users
                    model_obj = user_data  # Return as dict for now
                    model_type = "GitHubUser"
                    print("✅ Extracted user data (simplified)")
                except Exception as e:
                    print(f"❌ Error converting user: {e}")
                    sys.exit(1)
            else:
                print("❌ Generic Person type not yet supported for reverse conversion")
                sys.exit(1)
        else:
            print(f"❌ Unknown entity type: {entity_type}")
            sys.exit(1)
    else:
        print("❌ No entities found in JSON-LD graph")
        sys.exit(1)

    if model_obj is None:
        print("❌ Error: Could not convert JSON-LD")
        sys.exit(1)

    print("🔄 Serializing to JSON...")

    # Convert to dict
    if hasattr(model_obj, "model_dump"):
        data = model_obj.model_dump(exclude_none=True, exclude_unset=True)
    else:
        data = model_obj  # Already a dict

    print(f"💾 Writing JSON to: {output_file}")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    print("✅ Conversion complete!")
    print("\n📊 Summary:")
    print(f"   - Type:   {model_type}")
    print(f"   - Input:  {input_file} ({input_file.stat().st_size:,} bytes)")
    print(f"   - Output: {output_file} ({output_file.stat().st_size:,} bytes)")


def main():
    parser = argparse.ArgumentParser(
        description="Convert between JSON and JSON-LD formats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert Pydantic JSON to JSON-LD
  python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld

  # Convert JSON-LD to Pydantic JSON
  python scripts/convert_json_jsonld.py to-json input.jsonld output.json

  # With custom base URL
  python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld \\
      --base-url https://github.com/user/repo
        """,
    )

    parser.add_argument(
        "command",
        choices=["to-jsonld", "to-json"],
        help="Conversion direction",
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Input file path",
    )

    parser.add_argument(
        "output",
        type=Path,
        help="Output file path",
    )

    parser.add_argument(
        "--base-url",
        type=str,
        help="Base URL for @id generation (only for to-jsonld)",
    )

    args = parser.parse_args()

    # Check input file exists
    if not args.input.exists():
        print(f"❌ Error: Input file not found: {args.input}")
        sys.exit(1)

    # Create output directory if needed
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Run conversion
    try:
        if args.command == "to-jsonld":
            convert_to_jsonld(args.input, args.output, args.base_url)
        else:  # to-json
            if args.base_url:
                print("⚠️  Warning: --base-url is ignored for to-json conversion")
            convert_to_json(args.input, args.output)
    except Exception as e:
        print(f"\n❌ Conversion failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
