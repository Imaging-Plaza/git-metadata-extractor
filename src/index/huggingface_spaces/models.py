"""Pydantic model for an HF Space card."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class SpaceRecord(BaseModel):
    """Structured view of an HF Space persisted to DuckDB.

    Spaces differ from models/datasets by running an interactive demo
    (Gradio / Streamlit / Docker / static), so the schema carries
    `sdk`, `runtime_stage`, and `hardware` instead of pipeline/library.
    """

    repo_id: str
    author: Optional[str] = None
    sha: Optional[str] = None
    sdk: Optional[str] = None  # "gradio" | "streamlit" | "docker" | "static"
    runtime_stage: Optional[str] = None  # "RUNNING" | "SLEEPING" | "PAUSED" | …
    hardware: Optional[str] = None  # "cpu-basic" | "t4-small" | …
    license: Optional[str] = None
    likes: int = 0
    created_at: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    tags: list[str] = []
    card_data: dict[str, Any] = {}
    raw: dict[str, Any] = {}
