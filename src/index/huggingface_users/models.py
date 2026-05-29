"""Pydantic model for an HF user namespace card.

Named ``HFUserRecord`` (not ``UserRecord``) to disambiguate from
``src.index.github_users.models.UserRecord`` in cross-module imports.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class HFUserRecord(BaseModel):
    """Structured view of an HF user namespace persisted to DuckDB.

    HF's namespace overview returns a user's display name, bio, avatar,
    and counts of public models/datasets/spaces under that namespace.
    """

    slug: str  # namespace handle, e.g. "ylecun"
    fullname: Optional[str] = None  # display name
    details: Optional[str] = None   # bio
    avatar_url: Optional[str] = None
    num_models: int = 0
    num_datasets: int = 0
    num_spaces: int = 0
    num_followers: int = 0
    raw: dict[str, Any] = {}
