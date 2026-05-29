"""Pydantic model for a GitHub organization card."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class OrgRecord(BaseModel):
    """Structured view of a GitHub organization persisted to DuckDB.

    `login` is the org handle (e.g. `EPFL-ENAC`). Field set is the
    intersection of `/orgs/{org}` and `/users/{login}` payloads — every
    org GitHub returns has at minimum `login`, `id`, `description`,
    `public_repos`, `followers`. Optional `is_verified` is org-only.
    """

    login: str
    github_id: Optional[int] = None
    node_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    blog: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    twitter_username: Optional[str] = None
    company: Optional[str] = None
    public_repos: int = 0
    public_gists: int = 0
    followers: int = 0
    following: int = 0
    is_verified: Optional[bool] = None
    has_organization_projects: Optional[bool] = None
    has_repository_projects: Optional[bool] = None
    account_type: Optional[str] = None  # "Organization"
    avatar_url: Optional[str] = None
    html_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    raw: dict[str, Any] = {}
