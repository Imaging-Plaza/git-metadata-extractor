"""Pydantic models for the communities index."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class CommunityRecord(BaseModel):
    """One row of the `communities` table.

    `community_id` is namespaced by source (`zenodo:epfl-chili`) so we
    can ingest the same logical community from multiple sources in the
    future without primary-key clashes.
    """

    community_id: str
    source: str                       # 'zenodo', 'github', ...
    source_slug: str                  # raw slug from the source platform
    parent_org: Optional[str] = None  # 'epfl' | 'ethz' | 'cern' | 'cern_openlab'
    title: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    visibility: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    curator_names: List[str] = Field(default_factory=list)
    member_count: Optional[int] = None
    record_count: Optional[int] = None
    keywords: List[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
