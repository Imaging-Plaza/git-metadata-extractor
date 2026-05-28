"""Pydantic schemas for the structured columns persisted into DuckDB.

The full Zenodo record JSON is also stored in the `raw` column of `records`,
so these row models stay narrow — only the fields we filter / join / display.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class RecordRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    zenodo_id: str
    doi: str | None = None
    title: str | None = None
    description: str | None = None
    publication_date: date | None = None
    resource_type: str | None = None
    access_right: str | None = None
    license_id: str | None = None


class CreatorRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    creator_key: str
    display_name: str | None = None
    orcid: str | None = None
    affiliation: str | None = None


class CommunityRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    community_id: str
    title: str | None = None


class FileRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    record_id: str
    file_key: str
    file_id: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    download_url: str | None = None
