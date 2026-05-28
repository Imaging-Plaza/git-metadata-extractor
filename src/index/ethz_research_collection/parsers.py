"""DSpace JSON → Pydantic record parsers.

Centralises the metadata-key conventions DSpace uses so the indexing
stages don't repeat dict-walking. All inputs are raw item JSON dicts as
returned by `/server/api/core/items/{uuid}` or as embedded inside
`/discover/search/objects`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import ArticleRecord, OrganizationRecord, PersonRecord

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def first_value(metadata: dict, field: str) -> Optional[str]:
    values = metadata.get(field) or []
    if not isinstance(values, list) or not values:
        return None
    entry = values[0]
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def all_values(metadata: dict, field: str) -> List[str]:
    out: List[str] = []
    for entry in metadata.get(field) or []:
        if isinstance(entry, dict):
            v = entry.get("value")
            if v:
                out.append(v)
    return out


def first_authority(metadata: dict, field: str) -> Optional[str]:
    for entry in metadata.get(field) or []:
        if isinstance(entry, dict):
            a = entry.get("authority")
            if a:
                return a
    return None


def _research_collection_url(uuid: str, entity: str) -> str:
    return f"https://www.research-collection.ethz.ch/entities/{entity}/{uuid}"


def _year_from_date(date: Optional[str]) -> Optional[int]:
    if not date:
        return None
    m = _YEAR_RE.search(date)
    return int(m.group(0)) if m else None


def parse_article(item: Dict[str, Any], matched_urls: Optional[List[str]] = None) -> ArticleRecord:
    md = item.get("metadata", {}) or {}
    uuid = item.get("uuid") or ""
    publication_date = first_value(md, "dc.date.issued")
    # ETH RC stores per-author UUIDs in `relation.isAuthorOfPublication[*].value`
    # (see `extract_relations.py`); `dc.contributor.author.authority` is a
    # virtual slot id (`virtual::N`), not a UUID.
    author_uuids = [
        entry.get("value")
        for entry in (md.get("relation.isAuthorOfPublication") or [])
        if isinstance(entry, dict) and entry.get("value")
    ]
    return ArticleRecord(
        article_uuid=uuid,
        title=first_value(md, "dc.title"),
        abstract=first_value(md, "dc.description.abstract"),
        keywords=all_values(md, "dc.subject"),
        subjects=all_values(md, "dc.subject"),
        authors=all_values(md, "dc.contributor.author"),
        author_uuids=author_uuids,
        doi=first_value(md, "dc.identifier.doi"),
        publication_date=publication_date,
        year=_year_from_date(publication_date),
        publication_type=first_value(md, "dc.type"),
        language=first_value(md, "dc.language.iso"),
        # Journal: prefer ETH-side fields when present (ETH RC populates
        # ``ethz.journal.title``; EPFL Infoscience uses ``dc.relation.journal``).
        journal=(
            first_value(md, "ethz.journal.title")
            or first_value(md, "dc.relation.journal")
        ),
        journal_uuid=first_authority(md, "dc.relation.journal"),
        scopus_id=first_value(md, "ethz.identifier.scopus"),
        wos_id=first_value(md, "ethz.identifier.wos"),
        journal_volume=first_value(md, "ethz.journal.volume"),
        journal_issue=first_value(md, "ethz.journal.issue"),
        pages_start=first_value(md, "ethz.pages.start"),
        journal_abbreviated=first_value(md, "ethz.journal.abbreviated"),
        publisher=first_value(md, "dc.publisher"),
        issn=first_value(md, "dc.identifier.issn"),
        handle_uri=first_value(md, "dc.identifier.uri"),
        lab=first_value(md, "cris.virtual.department"),
        lab_uuid=first_authority(md, "cris.virtual.department"),
        org_uuids=sorted({
            entry.get("authority")
            for field in ("cris.virtual.department",
                          "cris.virtual.parent-organization",
                          "oairecerif.author.affiliation")
            for entry in (md.get(field) or [])
            if isinstance(entry, dict) and entry.get("authority")
        }),
        research_collection_url=_research_collection_url(uuid, "publication"),
        matched_urls=matched_urls or [],
    )


def parse_person(item: Dict[str, Any]) -> PersonRecord:
    md = item.get("metadata", {}) or {}
    uuid = item.get("uuid") or ""
    name = (
        first_value(md, "dc.title")
        or first_value(md, "person.name")
        or first_value(md, "person.familyName")
    )
    given = first_value(md, "person.givenName") or first_value(md, "eperson.firstname")
    family = first_value(md, "person.familyName") or first_value(md, "eperson.lastname")
    if not name and (given or family):
        name = " ".join(p for p in (given, family) if p)

    # ETH RC stores affiliation as a free-text "person.department" string
    # (e.g. "03996 - Benini, Luca / Benini, Luca"). Use it as
    # primary_affiliation when the DSpace-CRIS-style fields are absent.
    department_text = first_value(md, "person.department")
    primary_affiliation = (
        first_value(md, "person.affiliation.name") or department_text
    )

    edu_affiliation = first_value(md, "person.edu.affiliation")  # e.g. "faculty"
    return PersonRecord(
        person_uuid=uuid,
        name=name,
        given_name=given,
        family_name=family,
        orcid=first_value(md, "person.identifier.orcid"),
        sciper_id=first_value(md, "epfl.sciperId") or first_value(md, "cris.virtual.sciperId"),
        scopus_id=first_value(md, "person.identifier.scopus-author-id"),
        primary_affiliation=primary_affiliation,
        primary_affiliation_uuid=first_authority(md, "person.affiliation.name"),
        affiliation_uuids=sorted({
            entry.get("authority")
            for entry in (md.get("person.affiliation.name") or [])
            if isinstance(entry, dict) and entry.get("authority")
        }),
        position=first_value(md, "oairecerif.person.position") or edu_affiliation,
        biography=first_value(md, "dc.description") or first_value(md, "person.biography"),
        research_interests=all_values(md, "person.researchInterests"),
        profile_url=_research_collection_url(uuid, "person"),
    )


def parse_organization(item: Dict[str, Any]) -> OrganizationRecord:
    md = item.get("metadata", {}) or {}
    uuid = item.get("uuid") or ""
    parent_chain_authorities = [
        entry.get("authority")
        for entry in (md.get("cris.virtual.parent-organization") or [])
        if isinstance(entry, dict) and entry.get("authority")
    ]
    parent_chain_names = all_values(md, "cris.virtual.parent-organization")
    return OrganizationRecord(
        org_uuid=uuid,
        name=first_value(md, "dc.title") or first_value(md, "organization.legalName"),
        acronym=first_value(md, "organization.identifier.acronym"),
        aliases=all_values(md, "organization.alternateName"),
        parent_org_uuid=parent_chain_authorities[0] if parent_chain_authorities else None,
        parent_org_chain=parent_chain_authorities,
        parent_org_chain_names=parent_chain_names,
        description=first_value(md, "dc.description")
        or first_value(md, "dc.description.abstract"),
        sciper_unit_id=first_value(md, "cris.virtual.unitId")
        or first_value(md, "epfl.unitId"),
        unit_manager_uuid=first_authority(md, "cris.virtual.unitManager"),
        unit_manager_name=first_value(md, "cris.virtual.unitManager"),
        research_collection_url=_research_collection_url(uuid, "orgunit"),
    )
