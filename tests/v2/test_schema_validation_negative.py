from __future__ import annotations

from typing import Any, Callable

import pytest
from jsonschema import ValidationError, validate

INVALID_CASES = [
    pytest.param(
        "person_missing_name",
        "person",
        id="person-missing-required-schema-name",
    ),
    pytest.param(
        "person_bad_orcid",
        "person",
        id="person-invalid-orcid-pattern",
    ),
    pytest.param(
        "person_no_identifier",
        "person",
        id="person-missing-github-email-infoscience-anyof",
    ),
    pytest.param(
        "repo_bad_github_handle",
        "repository",
        id="repository-invalid-github-handle-pattern",
    ),
    pytest.param(
        "org_unknown_type",
        "organization",
        id="organization-unknown-type-enum",
    ),
    pytest.param(
        "membership_extra_properties",
        "membership",
        id="membership-extra-property-additionalProperties-false",
    ),
    pytest.param(
        "contribution_negative_count",
        "contribution",
        id="contribution-negative-count-minimum-zero",
    ),
    pytest.param(
        "article_bad_doi",
        "article",
        id="article-invalid-doi-pattern",
    ),
]

MIN_INVALID_FIXTURE_COUNT = 8


@pytest.mark.parametrize(("fixture_name", "schema_name"), INVALID_CASES)
def test_invalid_fixture_is_rejected_by_strict_schema(
    load_schema: Callable[[str, str], dict[str, Any]],
    load_fixture: Callable[[str, str], Any],
    fixture_name: str,
    schema_name: str,
) -> None:
    schema = load_schema("strict", schema_name)
    invalid_instance = load_fixture("schema/invalid", fixture_name)

    with pytest.raises(ValidationError):
        validate(instance=invalid_instance, schema=schema)


def test_invalid_fixture_catalog_has_minimum_coverage() -> None:
    assert len(INVALID_CASES) >= MIN_INVALID_FIXTURE_COUNT
