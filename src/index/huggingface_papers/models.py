"""Pydantic model for a HuggingFace Papers card."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class PaperAuthor(BaseModel):
    """One author as returned by `/api/papers/{arxiv_id}`. HF augments
    each author entry with their internal user record when matched."""

    name: str
    hidden: bool = False
    user_id: Optional[str] = None  # HF user id (when the author has an HF account)
    affiliation: Optional[str] = None


class PaperRecord(BaseModel):
    """Structured view of an HF Papers card persisted to DuckDB.

    `arxiv_id` is the natural primary key — both arXiv's canonical id
    (`YYMM.NNNNN` or `YYMM.NNNNNvN`) and the HF papers URL slug share
    the same form. We strip any trailing `vN` version suffix so
    `2310.01234` and `2310.01234v2` collapse to one row.
    """

    arxiv_id: str  # e.g. "2310.01234" — version suffix stripped
    title: str
    summary: Optional[str] = None  # the abstract
    doi: Optional[str] = None  # arxiv DOI, e.g. "10.48550/arXiv.2310.01234"
    authors: list[PaperAuthor] = []
    published_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    # HF-specific signal — pages curate "daily papers", which is a
    # strong relevance prior for search ranking.
    upvotes: int = 0
    num_comments: int = 0
    is_author_participating: Optional[bool] = None
    ai_summary: Optional[str] = None      # HF-generated TL;DR
    ai_keywords: list[str] = []           # HF-extracted keywords
    thumbnail: Optional[str] = None
    # Linked HF artifacts — JSON-encoded lists of {repo_id, type, ...} dicts.
    linked_models: list[dict[str, Any]] = []
    linked_datasets: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
