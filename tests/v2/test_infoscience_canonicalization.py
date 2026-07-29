"""Tests for the shared Infoscience canonical-URL helpers."""

from __future__ import annotations

import pytest

from git_metadata_extractor.canonicalization.infoscience import (
    infoscience_article_iri,
    infoscience_org_iri,
    infoscience_person_iri,
    parse_infoscience_iri,
)

_UUID = "f97b60da-bcab-4f2e-ba12-0ee0c4d0d6eb"
_PERSON_URL = f"https://infoscience.epfl.ch/entities/person/{_UUID}"
_ORG_URL = f"https://infoscience.epfl.ch/entities/orgunit/{_UUID}"
_ARTICLE_URL = f"https://infoscience.epfl.ch/entities/publication/{_UUID}"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_person_iri_from_bare_uuid():
    assert infoscience_person_iri(_UUID) == _PERSON_URL


def test_person_iri_from_canonical_url_is_idempotent():
    assert infoscience_person_iri(_PERSON_URL) == _PERSON_URL


def test_person_iri_handles_trailing_slash():
    assert infoscience_person_iri(_PERSON_URL + "/") == _PERSON_URL


def test_person_iri_handles_whitespace():
    assert infoscience_person_iri(f"  {_UUID}  ") == _PERSON_URL


def test_org_iri_builds_orgunit_url_not_organization():
    """Infoscience's URL uses `orgunit`, not `organization`."""
    assert infoscience_org_iri(_UUID) == _ORG_URL
    assert "/entities/orgunit/" in infoscience_org_iri(_UUID)


def test_article_iri_builds_publication_url():
    assert infoscience_article_iri(_UUID) == _ARTICLE_URL


# ---------------------------------------------------------------------------
# Type-mismatch rejection
# ---------------------------------------------------------------------------


def test_person_iri_rejects_url_for_wrong_kind():
    """Passing an org URL to `infoscience_person_iri` is a caller
    mistake — surface it loudly rather than producing the wrong URL."""
    assert infoscience_person_iri(_ORG_URL) is None
    assert infoscience_person_iri(_ARTICLE_URL) is None


def test_org_iri_rejects_url_for_wrong_kind():
    assert infoscience_org_iri(_PERSON_URL) is None
    assert infoscience_org_iri(_ARTICLE_URL) is None


def test_article_iri_rejects_url_for_wrong_kind():
    assert infoscience_article_iri(_PERSON_URL) is None
    assert infoscience_article_iri(_ORG_URL) is None


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not-a-uuid",
        "f97b60da",                         # too short
        "f97b60da-bcab-4f2e-ba12",          # incomplete
        "https://example.com/" + _UUID,     # wrong host
        "https://infoscience.epfl.ch/" + _UUID,  # missing /entities/<kind>/
        "https://infoscience.epfl.ch/entities/" + _UUID,  # missing /<kind>/
    ],
)
def test_helpers_return_none_for_malformed_input(raw):
    assert infoscience_person_iri(raw) is None
    assert infoscience_org_iri(raw) is None
    assert infoscience_article_iri(raw) is None


def test_uuid_must_be_v4_not_v1_or_v3():
    """The regex enforces UUID4 (the `4` nibble + `[89ab]` variant
    bits). Other UUID versions are rejected."""
    # UUID1-shaped string (third group starts with `1` not `4`).
    assert infoscience_person_iri("f97b60da-bcab-1f2e-ba12-0ee0c4d0d6eb") is None


# ---------------------------------------------------------------------------
# parse_infoscience_iri (inverse)
# ---------------------------------------------------------------------------


def test_parse_returns_kind_and_uuid_for_canonical_url():
    assert parse_infoscience_iri(_PERSON_URL) == ("person", _UUID)
    assert parse_infoscience_iri(_ORG_URL) == ("orgunit", _UUID)
    assert parse_infoscience_iri(_ARTICLE_URL) == ("publication", _UUID)


def test_parse_handles_trailing_slash():
    assert parse_infoscience_iri(_PERSON_URL + "/") == ("person", _UUID)


def test_parse_returns_none_on_non_canonical_input():
    assert parse_infoscience_iri(None) is None
    assert parse_infoscience_iri(_UUID) is None  # bare uuid is not a parseable URL
    assert parse_infoscience_iri("https://example.com/whatever") is None
    # Unknown kind path-segment.
    assert parse_infoscience_iri(
        f"https://infoscience.epfl.ch/entities/spaceship/{_UUID}",
    ) is None
