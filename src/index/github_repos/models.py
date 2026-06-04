"""Pydantic models for the GitHub index module.

`RepoRecord` is the structured view we persist into DuckDB. `raw` carries
the unparsed REST `repos/{owner}/{name}` payload for debugging and future
field additions without re-ingesting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class ContributorEntry(BaseModel):
    login: str
    contributions: int = 0


class RepoRecord(BaseModel):
    repo_id: str  # canonical URL: https://github.com/<owner>/<name>
    owner: str
    name: str
    default_branch: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    primary_language: Optional[str] = None
    languages: dict[str, int] = {}
    topics: list[str] = []
    license_spdx: Optional[str] = None
    is_fork: bool = False
    is_archived: bool = False
    is_private: bool = False
    stargazers_count: int = 0
    forks_count: int = 0
    watchers_count: int = 0
    open_issues_count: int = 0
    size_kb: int = 0
    created_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None
    readme_path: Optional[str] = None
    readme_text: Optional[str] = None
    contributors: list[ContributorEntry] = []
    raw: dict[str, Any] = {}
