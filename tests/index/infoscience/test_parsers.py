"""DSpace JSON → record parsers exercise."""

from __future__ import annotations

from src.index.infoscience.parsers import (
    parse_article,
    parse_organization,
    parse_person,
)


_PUB = "https://infoscience.epfl.ch/entities/publication/"
_PERSON = "https://infoscience.epfl.ch/entities/person/"
_ORGUNIT = "https://infoscience.epfl.ch/entities/orgunit/"


def test_parse_article(article_json: dict) -> None:
    rec = parse_article(article_json, matched_urls=["https://github.com/x/y"])
    # v3.0.0: the id is the canonical publication URL, and UUID cross-refs
    # (authors -> person, orgs -> orgunit) are URL-ified too.
    assert rec.article_uuid.startswith(_PUB)
    assert rec.article_uuid == rec.infoscience_url
    assert rec.title
    assert rec.authors, "expected at least one author name"
    assert any(rec.author_uuids)
    assert all(u.startswith(_PERSON) for u in rec.author_uuids)
    assert all(u.startswith(_ORGUNIT) for u in rec.org_uuids)
    assert rec.matched_urls == ["https://github.com/x/y"]
    assert rec.infoscience_url.startswith(_PUB)


def test_parse_person(person_json: dict) -> None:
    rec = parse_person(person_json)
    assert rec.person_uuid.startswith(_PERSON)
    assert rec.name
    assert rec.profile_url.startswith(_PERSON)


def test_parse_organization(organization_json: dict) -> None:
    rec = parse_organization(organization_json)
    assert rec.org_uuid.startswith(_ORGUNIT)
    assert rec.name
    assert rec.infoscience_url.startswith(_ORGUNIT)
