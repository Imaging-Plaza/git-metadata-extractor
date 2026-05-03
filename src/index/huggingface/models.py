"""Pydantic schemas for HuggingFace entities persisted into DuckDB.

Compact projections — only the fields we filter/join on as columns; the
full HF API payload lives in the `raw` JSON column.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

EntityType = Literal["models", "datasets", "spaces", "orgs"]

# Repo-shaped entity types ingested via list_*/info APIs.
ALL_ENTITY_TYPES: tuple[EntityType, ...] = ("models", "datasets", "spaces")

# All entity types embedded into Qdrant (repo-shaped + namespace-shaped).
ALL_EMBEDDABLE_TYPES: tuple[EntityType, ...] = (*ALL_ENTITY_TYPES, "orgs")

# Map plural CLI/table names to singular DuckDB chunk entity_type values.
ENTITY_TYPE_SINGULAR: dict[EntityType, str] = {
    "models": "model",
    "datasets": "dataset",
    "spaces": "space",
    "orgs": "org",
}

# `expand=[...]` arg passed to model_info / dataset_info / space_info.
# The Hub validates each expand value against an entity-specific allow-list,
# so we keep three distinct tuples. License is *not* a top-level expand on
# any endpoint — it ships inside `cardData` (the YAML front-matter).

MODEL_EXPAND_FIELDS: tuple[str, ...] = (
    "author",
    "cardData",
    "createdAt",
    "downloads",
    "downloadsAllTime",
    "gated",
    "lastModified",
    "library_name",
    "likes",
    "pipeline_tag",
    "private",
    "sha",
    "siblings",
    "tags",
)

DATASET_EXPAND_FIELDS: tuple[str, ...] = (
    "author",
    "cardData",
    "createdAt",
    "downloads",
    "downloadsAllTime",
    "gated",
    "lastModified",
    "likes",
    "private",
    "sha",
    "siblings",
    "tags",
)

SPACE_EXPAND_FIELDS: tuple[str, ...] = (
    "author",
    "cardData",
    "createdAt",
    "lastModified",
    "likes",
    "private",
    "runtime",
    "sdk",
    "sha",
    "siblings",
    "tags",
)


class OrgRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str
    namespace_kind: Literal["user", "org"] = "org"
    source: Literal["seed", "discover"] = "seed"
    scope: str  # 'epfl' | 'switzerland'


class ModelRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo_id: str
    author: str | None = None
    sha: str | None = None
    pipeline_tag: str | None = None
    library_name: str | None = None
    license: str | None = None
    downloads: int | None = None
    downloads_all_time: int | None = None
    likes: int | None = None
    gated: bool | None = None
    private: bool | None = None
    created_at: datetime | None = None
    last_modified: datetime | None = None


class DatasetRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo_id: str
    author: str | None = None
    sha: str | None = None
    license: str | None = None
    downloads: int | None = None
    downloads_all_time: int | None = None
    likes: int | None = None
    gated: bool | None = None
    private: bool | None = None
    created_at: datetime | None = None
    last_modified: datetime | None = None


class SpaceRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo_id: str
    author: str | None = None
    sha: str | None = None
    sdk: str | None = None
    runtime_stage: str | None = None
    hardware: str | None = None
    license: str | None = None
    likes: int | None = None
    created_at: datetime | None = None
    last_modified: datetime | None = None


class DiscoveryCandidate(BaseModel):
    """A namespace surfaced by `discover-orgs` for human review."""

    model_config = ConfigDict(extra="ignore")

    namespace: str
    hits: int
    sample_repo_ids: list[str]
    matched_terms: list[str]
    in_seed: bool
