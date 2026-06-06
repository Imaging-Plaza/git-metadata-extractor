from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class GitLabGroupRecord(BaseModel):
    group_id: str               # canonical web_url (https://<host>/groups/<full_path>)
    host: str
    full_path: str
    name: str | None = None
    description: str | None = None
    visibility: str | None = None
    parent: str | None = None   # parent group canonical URL or None
    web_url: str | None = None
    raw: dict[str, Any] = {}


class GitLabUserRecord(BaseModel):
    user_id: str                # canonical web_url (https://<host>/<username>)
    host: str
    username: str
    name: str | None = None
    bio: str | None = None
    location: str | None = None
    organization: str | None = None
    job_title: str | None = None
    public_email: str | None = None
    website_url: str | None = None
    linkedin: str | None = None
    twitter: str | None = None
    avatar_url: str | None = None
    web_url: str | None = None
    raw: dict[str, Any] = {}


class GitLabProjectRecord(BaseModel):
    project_id: str            # canonical web_url
    host: str
    full_path: str
    name: str | None = None
    description: str | None = None
    visibility: str | None = None
    is_fork: bool = False
    forked_from: str | None = None
    namespace: str | None = None
    topics: list[str] = []
    star_count: int = 0
    forks_count: int = 0
    default_branch: str | None = None
    last_activity_at: datetime | None = None
    created_at: datetime | None = None
    raw: dict[str, Any] = {}
