"""Pydantic model for a GitHub user card."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class UserRecord(BaseModel):
    """Structured view of a GitHub user persisted to DuckDB.

    `login` is the natural primary key. `github_id` is the numeric
    user id GitHub assigns (stable across login renames).
    """

    login: str
    github_id: Optional[int] = None
    node_id: Optional[str] = None
    name: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    blog: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    twitter_username: Optional[str] = None
    hireable: Optional[bool] = None
    public_repos: int = 0
    public_gists: int = 0
    followers: int = 0
    following: int = 0
    account_type: Optional[str] = None  # "User" (vs Organization)
    avatar_url: Optional[str] = None
    html_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    raw: dict[str, Any] = {}
