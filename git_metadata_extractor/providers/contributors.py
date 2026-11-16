"""Contributor enumeration evidence, separate from canonical graph properties."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, computed_field

from git_metadata_extractor.models import DataModel


class ContributorCoverage(DataModel):
    """Completeness refers to the endpoint's linked GitHub accounts, not all authors."""

    status: Literal["complete", "empty", "partial", "unavailable", "unknown"]
    source: Literal["gimie", "github_rest", "github_rest_commits", "provider"]
    scope: Literal["linked_github_accounts", "recent_commit_authors"] = "linked_github_accounts"
    reason: str | None = None
    fallback_reason: str | None = None
    pages_fetched: int = 0
    returned_count: int = 0


class ContributorLookup(DataModel):
    contributors: list[dict[str, Any]] = Field(default_factory=list)
    coverage: ContributorCoverage

    @computed_field
    @property
    def fetched(self) -> bool:
        """The observation adapter marks unavailable returns as failed retrievals."""
        return self.coverage.status != "unavailable"
