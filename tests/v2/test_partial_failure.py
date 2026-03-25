from __future__ import annotations

import pytest

from src.v2.pipeline.stages.models import ReconciledEntities
from src.v2.pipeline.stages.output_assembly import (
    RootEntityValidationError,
    assemble_output,
)
from src.v2.quality.schema_validation import (
    BatchValidationResult,
    ValidationResult,
)

UNPROCESSABLE_ENTITY_STATUS = 422
EXPECTED_EXCLUDED_ENTITIES = 2
MIN_EXPECTED_WARNINGS = 3


def _repository_entity() -> dict:
    return {
        "id": "https://github.com/owner/repo",
        "schema:name": "owner/repo",
        "schema:author": ["https://github.com/johndoe"],
        "pulse:ownedBy": "https://github.com/johndoe",
    }


def _person_entity() -> dict:
    return {
        "id": "https://github.com/johndoe",
        "schema:name": "John Doe",
    }


def _validation_error(path: str) -> ValidationResult:
    return ValidationResult(
        entity_type="person",
        is_valid=False,
        errors=[
            {
                "path": path,
                "message": "invalid value",
                "constraint": "pattern",
                "expected": "^[a-z]+$",
            },
        ],
    )


def test_assemble_output_includes_all_entities_when_all_are_valid() -> None:
    repository = _repository_entity()
    person = _person_entity()
    reconciled = ReconciledEntities(
        entities={
            "repositories": [repository],
            "persons": [person],
        },
        memberships=[],
        contributions=[],
        link_warnings=[],
    )
    strict = BatchValidationResult(
        valid_entities=[("repository", repository), ("person", person)],
        invalid_entities=[],
        warnings=[],
    )

    assembled = assemble_output(reconciled, strict)

    assert assembled.root_entity["id"] == repository["id"]
    assert any(entity["id"] == person["id"] for entity in assembled.related_entities)
    assert assembled.excluded_entities == []
    assert assembled.warnings == []


def test_assemble_output_excludes_invalid_non_root_entities_and_keeps_root() -> None:
    repository = _repository_entity()
    person = _person_entity()
    reconciled = ReconciledEntities(
        entities={"repositories": [repository], "persons": [person]},
        memberships=[],
        contributions=[],
        link_warnings=[],
    )
    strict = BatchValidationResult(
        valid_entities=[("repository", repository)],
        invalid_entities=[("person", person, _validation_error("schema:name"))],
        warnings=[],
    )

    assembled = assemble_output(reconciled, strict)

    assert assembled.root_entity["id"] == repository["id"]
    assert not any(entity["id"] == person["id"] for entity in assembled.related_entities)
    assert assembled.excluded_entities
    assert assembled.warnings


def test_assemble_output_raises_422_when_root_entity_fails_strict_validation() -> None:
    repository = _repository_entity()
    person = _person_entity()
    root_validation = ValidationResult(
        entity_type="repository",
        is_valid=False,
        errors=[
            {
                "path": "pulse:githubRepositoryHandle",
                "message": "missing property",
                "constraint": "required",
                "expected": "pulse:githubRepositoryHandle",
            },
        ],
    )

    reconciled = ReconciledEntities(
        entities={"repositories": [repository], "persons": [person]},
    )
    strict = BatchValidationResult(
        valid_entities=[],
        invalid_entities=[("repository", repository, root_validation)],
        warnings=[],
    )

    with pytest.raises(RootEntityValidationError) as raised:
        assemble_output(reconciled, strict)

    assert raised.value.status_code == UNPROCESSABLE_ENTITY_STATUS
    assert raised.value.entity_id == repository["id"]
    assert raised.value.validation_errors == root_validation.errors


def test_assemble_output_collects_all_warnings_for_multiple_non_root_failures() -> None:
    repository = _repository_entity()
    person = _person_entity()
    organization = {"id": "https://ror.org/05gzmn429", "schema:name": "EPFL"}
    reconciled = ReconciledEntities(
        entities={
            "repositories": [repository],
            "persons": [person],
            "organizations": [organization],
        },
        link_warnings=["pre-existing warning"],
    )
    strict = BatchValidationResult(
        valid_entities=[("repository", repository)],
        invalid_entities=[
            ("person", person, _validation_error("schema:name")),
            ("organization", organization, _validation_error("schema:identifier")),
        ],
    )

    assembled = assemble_output(reconciled, strict)

    assert len(assembled.excluded_entities) == EXPECTED_EXCLUDED_ENTITIES
    assert len(assembled.warnings) >= MIN_EXPECTED_WARNINGS
    assert "pre-existing warning" in assembled.warnings


def test_assemble_output_cleans_excluded_references_from_root_relationship_fields() -> None:
    repository = _repository_entity()
    person = _person_entity()
    reconciled = ReconciledEntities(
        entities={"repositories": [repository], "persons": [person]},
    )
    strict = BatchValidationResult(
        valid_entities=[("repository", repository)],
        invalid_entities=[("person", person, _validation_error("schema:name"))],
    )

    assembled = assemble_output(reconciled, strict)

    assert assembled.root_entity["schema:author"] == []
    assert assembled.root_entity["pulse:ownedBy"] is None


def test_assemble_output_records_reasons_for_excluded_entities() -> None:
    repository = _repository_entity()
    person = _person_entity()
    validation = _validation_error("schema:name")
    reconciled = ReconciledEntities(
        entities={"repositories": [repository], "persons": [person]},
    )
    strict = BatchValidationResult(
        valid_entities=[("repository", repository)],
        invalid_entities=[("person", person, validation)],
    )

    assembled = assemble_output(reconciled, strict)

    assert assembled.excluded_entities
    excluded = assembled.excluded_entities[0]
    assert excluded["entity"]["id"] == person["id"]
    assert excluded["reason"] == validation.errors
