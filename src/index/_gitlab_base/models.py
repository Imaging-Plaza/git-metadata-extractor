from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


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
