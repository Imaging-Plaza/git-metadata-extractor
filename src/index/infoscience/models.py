"""Pydantic models passed between pipeline stages.

The LanceDB row shapes live in `store.py` (PyArrow schemas, not Pydantic) so
the schemas aren't duplicated; these models cover on-disk JSON/JSONL and
in-memory transfer between stages.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DiscoverState(BaseModel):
    """Persisted under `discover_state.json` for resumability."""

    per_term_cursor: Dict[str, int] = Field(default_factory=dict)
    per_term_total: Dict[str, int] = Field(default_factory=dict)
    completed: Dict[str, bool] = Field(default_factory=dict)
    last_run_iso: Optional[str] = None


class MatchRecord(BaseModel):
    """One line in `matches.jsonl`."""

    uuid: str
    matched_urls: List[str] = Field(default_factory=list)
    counts_by_host: Dict[str, int] = Field(default_factory=dict)


class RelationRecord(BaseModel):
    """One line in `relations.jsonl`."""

    article_uuid: str
    person_uuids: List[str] = Field(default_factory=list)
    org_uuids: List[str] = Field(default_factory=list)


class ChunkRecord(BaseModel):
    """In-memory chunk before insertion into LanceDB."""

    chunk_id: str
    article_uuid: str
    chunk_index: int
    text: str
    title: Optional[str] = None
    abstract: Optional[str] = None
    authors: List[str] = Field(default_factory=list)
    author_uuids: List[str] = Field(default_factory=list)
    doi: Optional[str] = None
    publication_date: Optional[str] = None
    year: Optional[int] = None
    publication_type: Optional[str] = None
    language: Optional[str] = None
    subjects: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    lab: Optional[str] = None
    lab_uuid: Optional[str] = None
    org_uuids: List[str] = Field(default_factory=list)
    infoscience_url: Optional[str] = None
    matched_urls: List[str] = Field(default_factory=list)


class ArticleRecord(BaseModel):
    """One row of `infoscience_articles`."""

    article_uuid: str
    title: Optional[str] = None
    abstract: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    subjects: List[str] = Field(default_factory=list)
    authors: List[str] = Field(default_factory=list)
    author_uuids: List[str] = Field(default_factory=list)
    doi: Optional[str] = None
    publication_date: Optional[str] = None
    year: Optional[int] = None
    publication_type: Optional[str] = None
    language: Optional[str] = None
    journal: Optional[str] = None
    journal_uuid: Optional[str] = None
    lab: Optional[str] = None
    lab_uuid: Optional[str] = None
    org_uuids: List[str] = Field(default_factory=list)
    infoscience_url: Optional[str] = None
    matched_urls: List[str] = Field(default_factory=list)
    chunk_count: int = 0


class PersonRecord(BaseModel):
    """One row of `infoscience_persons`."""

    person_uuid: str
    name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    orcid: Optional[str] = None
    sciper_id: Optional[str] = None
    scopus_id: Optional[str] = None
    email_hash: Optional[str] = None
    primary_affiliation: Optional[str] = None
    primary_affiliation_uuid: Optional[str] = None
    affiliation_uuids: List[str] = Field(default_factory=list)
    position: Optional[str] = None
    biography: Optional[str] = None
    research_interests: List[str] = Field(default_factory=list)
    profile_url: Optional[str] = None
    related_article_uuids: List[str] = Field(default_factory=list)


class OrganizationRecord(BaseModel):
    """One row of `infoscience_organizations`."""

    org_uuid: str
    name: Optional[str] = None
    acronym: Optional[str] = None
    # Alternative EPFL-internal codes; both extracted directly from
    # the DSpace metadata block and persisted alongside `acronym` so
    # SQL-keyed lookups work regardless of which code the consumer
    # happens to know.
    infoscience_code: Optional[str] = None
    unit_code: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    parent_org_uuid: Optional[str] = None
    parent_org_chain: List[str] = Field(default_factory=list)
    parent_org_chain_names: List[str] = Field(default_factory=list)
    parent_acronym: Optional[str] = None
    director_name: Optional[str] = None
    org_type_dspace: Optional[str] = None
    description: Optional[str] = None
    sciper_unit_id: Optional[str] = None
    ror_id: Optional[str] = None
    unit_manager_uuid: Optional[str] = None
    unit_manager_name: Optional[str] = None
    infoscience_url: Optional[str] = None
    related_article_uuids: List[str] = Field(default_factory=list)
