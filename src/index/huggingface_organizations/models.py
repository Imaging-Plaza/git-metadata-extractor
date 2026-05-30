"""Pydantic model for an HF organization namespace card."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class HFOrgRecord(BaseModel):
    """Structured view of an HF organization namespace persisted to DuckDB.

    Same schema shape as ``HFUserRecord`` but represents an
    organization namespace (e.g. ``Imaging-Plaza`` on HF, or ``EPFL``).
    Kept as a separate model rather than a shared `NamespaceRecord`
    to mirror the github_users / github_organizations split.
    """

    slug: str  # namespace handle, e.g. "openai"
    fullname: Optional[str] = None
    details: Optional[str] = None
    avatar_url: Optional[str] = None
    num_models: int = 0
    num_datasets: int = 0
    num_spaces: int = 0
    num_followers: int = 0
    raw: dict[str, Any] = {}
