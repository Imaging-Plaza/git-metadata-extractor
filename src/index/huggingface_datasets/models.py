"""Pydantic model for an HF dataset card."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class DatasetRecord(BaseModel):
    """Structured view of an HF dataset persisted to DuckDB.

    Differs from ModelRecord by carrying citation metadata (BibTeX text,
    paperswithcode URL, and DOIs extracted from the citation block) and
    the structured `dataset_info` block instead of pipeline/library
    fields.
    """

    repo_id: str
    author: Optional[str] = None
    sha: Optional[str] = None
    license: Optional[str] = None
    downloads: int = 0
    downloads_all_time: int = 0
    likes: int = 0
    gated: Optional[bool] = None
    private: Optional[bool] = None
    created_at: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    tags: list[str] = []
    card_data: dict[str, Any] = {}
    dataset_info: dict[str, Any] = {}
    citation_text: Optional[str] = None
    paperswithcode_url: Optional[str] = None
    citation_dois: list[str] = []
    raw: dict[str, Any] = {}
