"""Invariants over the JSON Schemas the service actually loads.

Replaces `test_promoted_strict_schemas.py` and `test_promoted_agent_schemas.py`,
which spent most of their assertions proving that a copy under
`dev/ontology-v2-json-response/a-001/json-schema/` was byte-identical to the
schemas in `git_metadata_extractor/schema/json/`. That copy is gone, so the
comparison has nothing left to say; what survives here is the part that was
never about copies:

- every schema the validators load is valid JSON Schema
- the agent (permissive) schema is a superset of the strict schema's property
  names, so a field that passes permissive validation at agent time cannot be
  unrepresentable at strict-validation time

Both read `git_metadata_extractor/schema/json/` directly — the files
`agents/models.py::load_agent_schema` and
`validation/schema_validation.py::_load_strict_schema` open at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema.validators import validator_for

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = REPO_ROOT / "git_metadata_extractor" / "schema" / "json"
AGENT_SCHEMA_DIR = SCHEMA_ROOT / "agent"
STRICT_SCHEMA_DIR = SCHEMA_ROOT / "strict"

SCHEMA_NAMES = (
    "person.schema.json",
    "repository.schema.json",
    "organization.schema.json",
    "membership.schema.json",
    "contribution.schema.json",
    "article.schema.json",
)


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


@pytest.mark.parametrize("directory", [AGENT_SCHEMA_DIR, STRICT_SCHEMA_DIR])
@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_schema_is_valid_jsonschema(directory: Path, schema_name: str) -> None:
    parsed = _load_json(directory / schema_name)

    validator_cls = validator_for(parsed)
    validator_cls.check_schema(parsed)


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_agent_schema_contains_all_strict_property_names(schema_name: str) -> None:
    """Permissive must be able to carry everything strict will demand.

    A field present in the strict schema but absent from the agent schema is
    unreachable: `validate_permissive` soft-drops what it does not know about,
    so the value never survives to `strict_validation`, which then reports it
    missing. The two schemas fail in opposite directions and the graph loses
    the field silently.
    """
    strict_fields = _collect_property_names(_load_json(STRICT_SCHEMA_DIR / schema_name))
    agent_fields = _collect_property_names(_load_json(AGENT_SCHEMA_DIR / schema_name))

    missing_fields = sorted(strict_fields - agent_fields)
    assert not missing_fields, (
        f"Agent schema '{schema_name}' is missing fields defined in strict schema: "
        f"{missing_fields}"
    )
