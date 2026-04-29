from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from src.v2.validation.schema_validation import (
    BatchValidationResult,
    StrictSchemaValidator,
    ValidationResult,
)

EXPECTED_INVALID_BATCH_COUNT = 2


def test_strict_validator_accepts_valid_person_fixture(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    person = load_fixture("schema/strict", "pulse_PersonShape")[0]

    result = validator.validate("person", person)

    assert isinstance(result, ValidationResult)
    assert result.is_valid is True
    assert result.errors == []
    assert result.entity_type == "person"


def test_strict_validator_rejects_invalid_orcid_pattern(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    invalid_person = load_fixture("schema/invalid", "person_bad_orcid")

    result = validator.validate("person", invalid_person)

    assert result.is_valid is False
    assert any(
        error["constraint"] == "pattern" and "orcid" in error["path"].lower()
        for error in result.errors
    )


def test_strict_validator_rejects_missing_required_repository_field(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    repository = deepcopy(load_fixture("schema/strict", "pulse_RepositoryShape")[0])
    repository.pop("pulse:githubRepositoryHandle")

    result = validator.validate("repository", repository)

    assert result.is_valid is False
    assert any(
        error["constraint"] == "required"
        and "pulse:githubRepositoryHandle" in error["message"]
        for error in result.errors
    )


def test_strict_validator_rejects_unknown_organization_enum(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    invalid_org = load_fixture("schema/invalid", "org_unknown_type")

    result = validator.validate("organization", invalid_org)

    assert result.is_valid is False
    assert any(
        error["constraint"] == "enum" and "pulse:OrganizationType" in error["path"]
        for error in result.errors
    )


def test_strict_validator_rejects_additional_properties(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    invalid_membership = load_fixture("schema/invalid", "membership_extra_properties")

    result = validator.validate("membership", invalid_membership)

    assert result.is_valid is False
    assert any(error["constraint"] == "additionalProperties" for error in result.errors)


def test_strict_validator_batch_result_splits_valid_and_invalid_entities(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    valid_person = load_fixture("schema/strict", "pulse_PersonShape")[0]
    invalid_person = load_fixture("schema/invalid", "person_bad_orcid")
    invalid_org = load_fixture("schema/invalid", "org_unknown_type")

    batch_result = validator.validate_batch(
        [
            ("person", valid_person),
            ("person", invalid_person),
            ("organization", invalid_org),
        ],
    )

    assert isinstance(batch_result, BatchValidationResult)
    assert len(batch_result.valid_entities) == 1
    assert len(batch_result.invalid_entities) == EXPECTED_INVALID_BATCH_COUNT
    assert batch_result.warnings


def test_strict_validator_errors_include_path_and_expected_constraint(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    invalid_person = load_fixture("schema/invalid", "person_bad_orcid")

    result = validator.validate("person", invalid_person)

    assert result.is_valid is False
    assert result.errors
    first_error = result.errors[0]
    assert first_error["path"]
    assert first_error["constraint"]
    assert first_error["expected"]


def test_strict_validator_accepts_prefixed_idsource_values(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    repository = load_fixture("schema/strict", "pulse_RepositoryShape")[0]
    repository["idSource"] = "pulse:githubRepositoryHandle"

    result = validator.validate("repository", repository)

    assert result.is_valid is True


def test_strict_validator_rejects_legacy_unprefixed_idsource_values(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    repository = load_fixture("schema/strict", "pulse_RepositoryShape")[0]
    repository["idSource"] = "githubRepositoryHandle"

    result = validator.validate("repository", repository)

    assert result.is_valid is False
    assert any(error["path"] == "idSource" and error["constraint"] == "enum" for error in result.errors)
