from typing import Any, Optional

from pydantic import BaseModel, Field

from ..data_models import GitAuthor, Person

# I need to simplify this, and have one single model for GithubUsers
# 1. Parse github user (username) -> GitHubUserMetadata
# 2. Attach GitHubUserMetadata to GitHubUser


class EnrichedAuthor(BaseModel):
    """Enriched author information"""

    name: str = Field(description="Author's name")
    email: Optional[str] = Field(description="Author's email address", default=None)
    orcidId: Optional[str] = Field(
        description="Author's ORCID identifier",
        default=None,
    )
    affiliations: list[str] = Field(
        description="List of all identified affiliations (current and historical)",
        default_factory=list,
    )
    currentAffiliation: Optional[str] = Field(
        description="Most recent or current affiliation",
        default=None,
    )
    affiliationHistory: list[dict[str, Any]] = Field(
        description="Temporal affiliation information with start/end dates when available",
        default_factory=list,
    )
    contributionSummary: Optional[str] = Field(
        description="Summary of the author's contributions to the repository",
        default=None,
    )
    confidenceScore: float = Field(
        description="Confidence score (0.0 to 1.0) for the enriched information",
        default=0.0,
    )
    additionalInfo: Optional[str] = Field(
        description="Additional biographical or professional information found",
        default=None,
    )


class UserEnrichmentResult(BaseModel):
    """Result of user enrichment analysis"""

    enrichedAuthors: list[EnrichedAuthor] = Field(
        description="List of enriched author information",
        default_factory=list,
    )
    summary: str = Field(
        description="Overall summary of the author affiliations and patterns",
    )


class UserAnalysisContext(BaseModel):
    """Context provided to the agent for analysis"""

    repository_url: str
    git_authors: list[GitAuthor]
    existing_authors: list[Person]
