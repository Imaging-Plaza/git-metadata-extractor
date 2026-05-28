import json
from pathlib import Path

import pytest
from jsonschema.validators import validator_for

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCHEMA_DIR = (
    REPO_ROOT / "dev" / "ontology-v2-json-response" / "a-001" / "json-schema" / "strict"
)
PROMOTED_SCHEMA_DIR = REPO_ROOT / "src" / "v2" / "schema" / "json" / "strict"

SCHEMA_FILE_MAP = {
    "person.schema.json": "pulse_PersonShape.schema.json",
    "repository.schema.json": "pulse_RepositoryShape.schema.json",
    "organization.schema.json": "pulse_OrganizationShape.schema.json",
    "membership.schema.json": "pulse_MembershipShape.schema.json",
    "contribution.schema.json": "pulse_ContributionShape.schema.json",
    "article.schema.json": "pulse_ArticleShape.schema.json",
}


@pytest.mark.parametrize(("promoted_name", "source_name"), SCHEMA_FILE_MAP.items())
def test_promoted_schema_is_valid_json(promoted_name: str, source_name: str) -> None:
    schema_path = PROMOTED_SCHEMA_DIR / promoted_name
    source_path = SOURCE_SCHEMA_DIR / source_name

    assert source_path.exists(), f"Missing source schema: {source_path}"
    assert schema_path.exists(), f"Missing promoted schema: {schema_path}"

    with schema_path.open(encoding="utf-8") as schema_file:
        parsed_schema = json.load(schema_file)

    assert isinstance(parsed_schema, dict)


@pytest.mark.parametrize(("promoted_name", "source_name"), SCHEMA_FILE_MAP.items())
def test_promoted_schema_is_byte_identical(promoted_name: str, source_name: str) -> None:
    schema_path = PROMOTED_SCHEMA_DIR / promoted_name
    source_path = SOURCE_SCHEMA_DIR / source_name

    assert schema_path.read_bytes() == source_path.read_bytes()


@pytest.mark.parametrize("promoted_name", SCHEMA_FILE_MAP)
def test_promoted_schema_is_jsonschema_valid(promoted_name: str) -> None:
    schema_path = PROMOTED_SCHEMA_DIR / promoted_name

    with schema_path.open(encoding="utf-8") as schema_file:
        parsed_schema = json.load(schema_file)

    validator_cls = validator_for(parsed_schema)
    validator_cls.check_schema(parsed_schema)
