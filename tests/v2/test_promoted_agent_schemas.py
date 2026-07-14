from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema.validators import validator_for

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCHEMA_DIR = (
    REPO_ROOT / "dev" / "ontology-v2-json-response" / "a-001" / "json-schema" / "agent"
)
PROMOTED_SCHEMA_DIR = REPO_ROOT / "git_metadata_extractor" / "schema" / "json" / "agent"
STRICT_SCHEMA_DIR = REPO_ROOT / "git_metadata_extractor" / "schema" / "json" / "strict"

SCHEMA_FILE_MAP = {
    "person.schema.json": "pulse_PersonShape.schema.json",
    "repository.schema.json": "pulse_RepositoryShape.schema.json",
    "organization.schema.json": "pulse_OrganizationShape.schema.json",
    "membership.schema.json": "pulse_MembershipShape.schema.json",
    "contribution.schema.json": "pulse_ContributionShape.schema.json",
    "article.schema.json": "pulse_ArticleShape.schema.json",
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as schema_file:
        parsed = json.load(schema_file)
    assert isinstance(parsed, dict)
    return parsed


def _collect_property_names(schema: dict[str, Any]) -> set[str]:
    property_names: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                property_names.update(properties.keys())
                for child in properties.values():
                    _walk(child)

            for keyword in (
                "allOf",
                "anyOf",
                "oneOf",
                "not",
                "if",
                "then",
                "else",
                "items",
                "additionalProperties",
                "definitions",
                "$defs",
            ):
                if keyword in node:
                    _walk(node[keyword])
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(schema)
    return property_names


@pytest.mark.parametrize(("promoted_name", "source_name"), SCHEMA_FILE_MAP.items())
def test_promoted_agent_schema_is_valid_json(promoted_name: str, source_name: str) -> None:
    schema_path = PROMOTED_SCHEMA_DIR / promoted_name
    source_path = SOURCE_SCHEMA_DIR / source_name

    assert source_path.exists(), f"Missing source schema: {source_path}"
    assert schema_path.exists(), f"Missing promoted schema: {schema_path}"

    parsed_schema = _load_json(schema_path)
    assert isinstance(parsed_schema, dict)



@pytest.mark.parametrize("promoted_name", SCHEMA_FILE_MAP)
def test_promoted_agent_schema_is_jsonschema_valid(promoted_name: str) -> None:
    schema_path = PROMOTED_SCHEMA_DIR / promoted_name
    parsed_schema = _load_json(schema_path)

    validator_cls = validator_for(parsed_schema)
    validator_cls.check_schema(parsed_schema)


@pytest.mark.parametrize("schema_name", SCHEMA_FILE_MAP)
def test_agent_schema_contains_all_strict_property_names(schema_name: str) -> None:
    strict_schema = _load_json(STRICT_SCHEMA_DIR / schema_name)
    agent_schema = _load_json(PROMOTED_SCHEMA_DIR / schema_name)

    strict_fields = _collect_property_names(strict_schema)
    agent_fields = _collect_property_names(agent_schema)

    missing_fields = sorted(strict_fields - agent_fields)
    assert not missing_fields, (
        f"Agent schema '{schema_name}' is missing fields defined in strict schema: "
        f"{missing_fields}"
    )
