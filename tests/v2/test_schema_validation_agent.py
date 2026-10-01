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
class AgentEntityCase:
    schema_name: str
    fixture_file_name: str


ENTITY_CASES = {
    "person": AgentEntityCase(
        schema_name="person",
        fixture_file_name="pulse_PersonShape.json",
    ),
    "repository": AgentEntityCase(
        schema_name="repository",
        fixture_file_name="pulse_RepositoryShape.json",
    ),
    "organization": AgentEntityCase(
        schema_name="organization",
        fixture_file_name="pulse_OrganizationShape.json",
    ),
    "membership": AgentEntityCase(
        schema_name="membership",
        fixture_file_name="pulse_MembershipShape.json",
    ),
    "contribution": AgentEntityCase(
        schema_name="contribution",
        fixture_file_name="pulse_ContributionShape.json",
    ),
    "article": AgentEntityCase(
        schema_name="article",
        fixture_file_name="pulse_ArticleShape.json",
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

INSTANCE_PARAMS = []
for entity, case in ENTITY_CASES.items():
    for instance_index, instance in enumerate(ENTITY_FIXTURES[entity]):
        instance_id = str(instance.get("id", f"index-{instance_index}"))
        INSTANCE_PARAMS.append(
            pytest.param(
                case.schema_name,
                instance,
                id=f"{entity}-{instance_index}-{instance_id}",
            ),
        )


@pytest.mark.parametrize(("schema_name", "instance"), INSTANCE_PARAMS)
def test_strict_fixture_instance_validates_against_agent_schema(
    load_schema: Callable[[str, str], dict[str, Any]],
    schema_name: str,
    instance: dict[str, Any],
) -> None:
    schema = load_schema("agent", schema_name)
    validate(instance=instance, schema=schema)
