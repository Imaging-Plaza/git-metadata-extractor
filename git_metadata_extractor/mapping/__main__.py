"""Read-only mapping validation and inventory: python -m git_metadata_extractor.mapping."""

import argparse
import json

from pydantic import TypeAdapter

from git_metadata_extractor.mapping import load_mapping, load_policy, mapping_manifest
from git_metadata_extractor.mapping.models import MappingSpec
from git_metadata_extractor.mapping.policies import (
    ExtractionPolicy,
    MergeRules,
    OntologyPolicy,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check",
        action="store_true",
        help="Validate all packaged mappings",
    )
    modes.add_argument(
        "--schema",
        action="store_true",
        help="Print the Pydantic-generated YAML contract",
    )
    args = parser.parse_args()
    if args.schema:
        contract = TypeAdapter(
            MappingSpec | ExtractionPolicy | MergeRules | OntologyPolicy,
        )
        print(json.dumps(contract.json_schema(), indent=2))
        return
    inventory = mapping_manifest()
    if args.check:
        print(
            f"Validated {len(inventory['mappings'])} mappings and "
            f"{len(inventory['policies'])} policies: {inventory['fingerprint']}",
        )
        return
    for entry in inventory["mappings"]:
        spec = load_mapping(entry["name"])
        entry["fields"] = {key: rule.model_dump() for key, rule in spec.fields.items()}
        entry["internal_fields"] = {
            key: rule.model_dump() for key, rule in spec.internal_fields.items()
        }
    inventory["policies"] = [
        load_policy(entry["name"]).model_dump() for entry in inventory["policies"]
    ]
    print(json.dumps(inventory, indent=2))


if __name__ == "__main__":
    main()
