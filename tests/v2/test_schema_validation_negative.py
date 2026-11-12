from __future__ import annotations

from typing import Any, Callable

import pytest

from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator

# Each fixture breaks exactly one constraint, named by its JSON Schema keyword.
# Asserting the keyword set, not just "invalid", is what stops a case passing
# for the wrong reason: a fixture that drifts out of date with some other
# constraint is still rejected, but by the wrong keyword.
INVALID_CASES = [
    pytest.param(
        "person_missing_name",
        "person",
        "required",
        id="person-missing-required-schema-name",
    ),
    pytest.param(
        "person_bad_orcid",
        "person",
        "pattern",
        id="person-invalid-orcid-pattern",
    ),
    pytest.param(
        "person_no_identifier",
        "person",
        "anyOf",
        id="person-missing-github-email-infoscience-anyof",
    ),
    pytest.param(
        "repo_bad_github_handle",
        "repository",
        "pattern",
        id="repository-invalid-github-handle-pattern",
    ),
    pytest.param(
        "org_unknown_type",
        "organization",
        "enum",
        id="organization-unknown-type-enum",
    ),
    pytest.param(
        "membership_extra_properties",
        "membership",
        "additionalProperties",
        id="membership-extra-property-additionalProperties-false",
    ),
    pytest.param(
        "contribution_negative_count",
        "contribution",
        "minimum",
        id="contribution-negative-count-minimum-zero",
    ),
    pytest.param(
        "article_bad_doi",
        "article",
        "pattern",
        id="article-invalid-doi-pattern",
    ),
]


@pytest.mark.parametrize(("fixture_name", "schema_name", "constraint"), INVALID_CASES)
def test_invalid_fixture_is_rejected_by_strict_schema(
    load_fixture: Callable[[str, str], Any],
    fixture_name: str,
    schema_name: str,
    constraint: str,
) -> None:
    invalid_instance = load_fixture("schema/invalid", fixture_name)

    result = StrictSchemaValidator().validate(schema_name, invalid_instance)

    assert result.is_valid is False
    assert {error["constraint"] for error in result.errors} == {constraint}
