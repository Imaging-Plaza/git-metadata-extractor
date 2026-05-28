from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GitHubURLType(str, Enum):
    REPOSITORY = "repository"
    USER = "user"
    ORGANIZATION = "organization"


@dataclass(frozen=True)
class GitHubURLClassification:
    normalized_url: str
    detected_type: GitHubURLType
    owner: str
    repo: str | None = None


class UnsupportedGitHubURL(ValueError):  # noqa: N818
    def __init__(self, reason: str, normalized_url: str) -> None:
        self.reason = reason
        self.normalized_url = normalized_url
        super().__init__(reason)
