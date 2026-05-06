"""Pydantic schemas for the ORCID indexer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

EntityType = Literal["persons", "employments", "educations"]
ALL_ENTITY_TYPES: tuple[EntityType, ...] = ("persons", "employments", "educations")

DiscoverySource = Literal["openalex", "orcid_search", "both", "manual"]


class PersonRow(BaseModel):
    """Structured columns persisted into DuckDB `persons`."""

    model_config = ConfigDict(extra="ignore")

    orcid_id: str
    given_name: str | None = None
    family_name: str | None = None
    display_name: str | None = None
    biography: str | None = None
    in_scope: bool = False
    scope_reason: str | None = None
    discovered_via: str = "manual"


class AffiliationRow(BaseModel):
    """One employment or education entry."""

    model_config = ConfigDict(extra="ignore")

    orcid_id: str
    seq: int
    organization: str
    org_ror: str | None = None
    department: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class SeedRecord(BaseModel):
    """One row of the discover-stage seed list."""

    model_config = ConfigDict(extra="ignore")

    orcid_id: str
    discovered_via: str
    hint: str | None = None
