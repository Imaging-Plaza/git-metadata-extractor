"""Pydantic schemas + OpenAlex `select` projections per entity.

We keep the projections compact — only fields we filter/join on in DuckDB or
embed for vectors. Everything else lives in the `raw` JSON column.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal[
    "works",
    "authors",
    "institutions",
    "sources",
    "topics",
    "concepts",
]

ALL_ENTITY_TYPES: tuple[EntityType, ...] = (
    "works",
    "authors",
    "institutions",
    "sources",
    "topics",
    "concepts",
)

# OpenAlex `select=` projections per entity. Comma-joined strings are passed
# straight to pyalex's `.select(...)` call.
WORKS_PROJECTION = (
    "id",
    "doi",
    "title",
    "abstract_inverted_index",
    "publication_year",
    "primary_topic",
    "primary_location",
    "authorships",
    "concepts",
    "topics",
    "type",
    "has_fulltext",
    "fulltext_origin",
)

AUTHORS_PROJECTION = (
    "id",
    "display_name",
    "orcid",
    "last_known_institutions",
    "affiliations",
    "works_count",
    "cited_by_count",
)

INSTITUTIONS_PROJECTION = (
    "id",
    "ror",
    "display_name",
    "country_code",
    "type",
    "lineage",
)

SOURCES_PROJECTION = (
    "id",
    "issn_l",
    "display_name",
    "type",
    "host_organization",
)

TOPICS_PROJECTION = (
    "id",
    "display_name",
    "domain",
    "field",
    "subfield",
)

CONCEPTS_PROJECTION = (
    "id",
    "display_name",
    "level",
    "wikidata",
)


class WorkRow(BaseModel):
    """Structured columns persisted into DuckDB `works`."""

    model_config = ConfigDict(extra="ignore")

    openalex_id: str
    doi: str | None = None
    title: str | None = None
    abstract: str | None = None
    publication_year: int | None = None
    primary_topic_id: str | None = None
    primary_source_id: str | None = None


class AuthorRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openalex_id: str
    display_name: str | None = None
    orcid: str | None = None
    last_known_institution_id: str | None = None


class InstitutionRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openalex_id: str
    ror: str | None = None
    display_name: str | None = None
    country_code: str | None = None


class SourceRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openalex_id: str
    issn_l: str | None = None
    display_name: str | None = None
    type: str | None = None


class TopicRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openalex_id: str
    display_name: str | None = None
    domain_id: str | None = None
    field_id: str | None = None


class ConceptRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openalex_id: str
    display_name: str | None = None
    level: int | None = None


class WorkGitHubURLRow(BaseModel):
    """A canonicalized GitHub URL extracted from a Work's text."""

    model_config = ConfigDict(extra="ignore")

    work_id: str
    url: str
    normalized_url: str
    owner: str | None = None
    repo: str | None = None
    source: Literal["abstract", "fulltext"] = Field(...)


class GitHubDiscoveryStats(BaseModel):
    """Summary of a `find-github` run."""

    works_seen: int = 0
    works_persisted: int = 0
    abstracts_scanned: int = 0
    urls_extracted: int = 0
    distinct_normalized_urls: int = 0
