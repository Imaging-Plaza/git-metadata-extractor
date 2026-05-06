from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import validate

REPO_ROOT = Path(__file__).resolve().parents[2]
STRICT_FIXTURE_DIR = REPO_ROOT / "tests" / "v2" / "fixtures" / "schema" / "strict"


@dataclass(frozen=True)
class StrictEntityCase:
    schema_name: str
    fixture_file_name: str
    minimum_count: int


ENTITY_CASES = {
    "person": StrictEntityCase(
        schema_name="person",
        fixture_file_name="pulse_PersonShape.json",
        minimum_count=5,
    ),
    "repository": StrictEntityCase(
        schema_name="repository",
        fixture_file_name="pulse_RepositoryShape.json",
        minimum_count=4,
    ),
    "organization": StrictEntityCase(
        schema_name="organization",
        fixture_file_name="pulse_OrganizationShape.json",
        minimum_count=5,
    ),
    "membership": StrictEntityCase(
        schema_name="membership",
        fixture_file_name="pulse_MembershipShape.json",
        minimum_count=6,
    ),
    "contribution": StrictEntityCase(
        schema_name="contribution",
        fixture_file_name="pulse_ContributionShape.json",
        minimum_count=9,
    ),
    "article": StrictEntityCase(
        schema_name="article",
        fixture_file_name="pulse_ArticleShape.json",
        minimum_count=4,
    ),
}


def _load_fixture_instances(file_name: str) -> list[dict[str, Any]]:
    fixture_path = STRICT_FIXTURE_DIR / file_name
    with fixture_path.open(encoding="utf-8") as fixture_file:
        parsed = json.load(fixture_file)

    assert isinstance(parsed, list), f"Expected fixture list in {fixture_path}"
    for index, item in enumerate(parsed):
        assert isinstance(item, dict), (
            f"Expected object at index {index} in fixture {fixture_path}"
        )

    return parsed


ENTITY_FIXTURES = {
    entity: _load_fixture_instances(case.fixture_file_name)
    for entity, case in ENTITY_CASES.items()
}

ENTITY_PARAMS = [
    pytest.param(entity, case, id=entity) for entity, case in ENTITY_CASES.items()
]

INSTANCE_PARAMS = []
for entity, case in ENTITY_CASES.items():
    for instance_index, instance in enumerate(ENTITY_FIXTURES[entity]):
        instance_id = str(instance.get("id", f"index-{instance_index}"))
        INSTANCE_PARAMS.append(
            pytest.param(
                entity,
                case.schema_name,
                instance_index,
                instance,
                id=f"{entity}-{instance_index}-{instance_id}",
            ),
        )


@pytest.mark.parametrize(("entity_type", "case"), ENTITY_PARAMS)
def test_strict_fixtures_have_required_minimum_instances(
    entity_type: str,
    case: StrictEntityCase,
) -> None:
    assert len(ENTITY_FIXTURES[entity_type]) >= case.minimum_count


@pytest.mark.parametrize(
    ("entity_type", "schema_name", "instance_index", "instance"),
    INSTANCE_PARAMS,
)
def test_strict_fixture_instance_validates_against_strict_schema(
    load_schema: Callable[[str, str], dict[str, Any]],
    entity_type: str,
    schema_name: str,
    instance_index: int,
    instance: dict[str, Any],
) -> None:
    assert entity_type in ENTITY_CASES
    assert instance_index >= 0
    schema = load_schema("strict", schema_name)
    validate(instance=instance, schema=schema)
