"""Pydantic schemas for the structured columns persisted into DuckDB.

The full payload from each REST endpoint is also stored as JSON in the
`raw` column on every table, so these row models stay narrow — only the
fields we filter / join / display.

`source_url` is required (NOT NULL in the schema): the user requirement
is that every entity preserves its canonical SWISSUbase URL so downstream
agents can link back.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class StudyRow(BaseModel):
    """A SWISSUbase study (called a ``Project`` in the UI)."""

    model_config = ConfigDict(extra="ignore")

    study_id: str
    ref: str | None = None
    title: str | None = None
    description: str | None = None
    description_language: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    progress: str | None = None
    main_discipline: str | None = None
    sub_discipline: str | None = None
    version: str | None = None
    data_availability: str | None = None
    dataset_count: int | None = None
    affiliation_match: bool = False
    source_url: str


class DatasetRow(BaseModel):
    """A SWISSUbase dataset (called a ``Resource`` in the UI). Child of a study."""

    model_config = ConfigDict(extra="ignore")

    dataset_id: str
    study_id: str
    title: str | None = None
    description: str | None = None
    access_right: str | None = None
    license_id: str | None = None
    file_count: int | None = None
    source_url: str


class PersonRow(BaseModel):
    """An author / principal investigator / collaborator referenced by studies."""

    model_config = ConfigDict(extra="ignore")

    person_key: str
    display_name: str | None = None
    orcid: str | None = None
    affiliation: str | None = None
    source_url: str | None = None


class InstitutionRow(BaseModel):
    """An institution referenced by studies."""

    model_config = ConfigDict(extra="ignore")

    institution_key: str
    name: str | None = None
    address: str | None = None
    ror_id: str | None = None
    source_url: str | None = None
