"""Pydantic model for an HF model card."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class ModelRecord(BaseModel):
    """Structured view of an HF model persisted to DuckDB.

    ``repo_id`` is the canonical URL (https://huggingface.co/<repo_id>).
    Mirrors the schema columns in ``storage/schema.sql``.
    """

    repo_id: str
    author: Optional[str] = None
    sha: Optional[str] = None
    pipeline_tag: Optional[str] = None
    library_name: Optional[str] = None
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
    base_models: list[str] = []
    # arXiv DOIs derived from `arxiv:<id>` tags, in canonical URL form
    # `https://doi.org/10.48550/arXiv.<id>`.
    arxiv_dois: list[str] = []
    raw: dict[str, Any] = {}
