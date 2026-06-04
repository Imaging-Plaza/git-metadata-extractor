"""Tests for the shared ETH Research Collection canonical-URL helpers."""

from __future__ import annotations

import pytest

from src.v2.canonicalization.ethz import (
    ethz_article_iri,
    ethz_org_iri,
    ethz_person_iri,
    parse_ethz_iri,
)

_UUID = "f97b60da-bcab-4f2e-ba12-0ee0c4d0d6eb"
_BASE = "https://www.research-collection.ethz.ch/entities"
_PERSON_URL = f"{_BASE}/person/{_UUID}"
_ORG_URL = f"{_BASE}/orgunit/{_UUID}"
_ARTICLE_URL = f"{_BASE}/publication/{_UUID}"


def test_person_iri_from_bare_uuid():
    assert ethz_person_iri(_UUID) == _PERSON_URL


def test_iri_idempotent_and_trailing_slash():
    assert ethz_person_iri(_PERSON_URL) == _PERSON_URL
    assert ethz_person_iri(_PERSON_URL + "/") == _PERSON_URL
    assert ethz_person_iri(f"  {_UUID}  ") == _PERSON_URL


def test_org_iri_builds_orgunit_url():
    assert ethz_org_iri(_UUID) == _ORG_URL
    assert "/entities/orgunit/" in ethz_org_iri(_UUID)


def test_article_iri_builds_publication_url():
    assert ethz_article_iri(_UUID) == _ARTICLE_URL


def test_iri_rejects_wrong_kind_url():
    assert ethz_person_iri(_ORG_URL) is None
    assert ethz_org_iri(_PERSON_URL) is None
    assert ethz_article_iri(_ORG_URL) is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not-a-uuid",
        "f97b60da",
        "https://example.com/" + _UUID,
        f"{_BASE}/{_UUID}",  # missing /<kind>/
        "f97b60da-bcab-1f2e-ba12-0ee0c4d0d6eb",  # UUID1-shaped
    ],
)
def test_helpers_return_none_for_malformed_input(raw):
    assert ethz_person_iri(raw) is None
    assert ethz_org_iri(raw) is None
    assert ethz_article_iri(raw) is None


def test_parse_returns_kind_and_uuid():
    assert parse_ethz_iri(_PERSON_URL) == ("person", _UUID)
    assert parse_ethz_iri(_ORG_URL) == ("orgunit", _UUID)
    assert parse_ethz_iri(_ARTICLE_URL) == ("publication", _UUID)
    assert parse_ethz_iri(_ARTICLE_URL + "/") == ("publication", _UUID)


def test_parse_returns_none_on_non_canonical():
    assert parse_ethz_iri(None) is None
    assert parse_ethz_iri(_UUID) is None
    assert parse_ethz_iri(f"{_BASE}/spaceship/{_UUID}") is None
