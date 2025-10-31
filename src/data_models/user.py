"""
User data models
"""

from __future__ import annotations

import re
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from pydantic import (
    BaseModel,
    Field,
    validator,
)

from .models import (
    Discipline,
    Organization,
    Person,
)
from .repository import GitAuthor, InfoscienceEntity


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
    infoscienceEntity: Optional[InfoscienceEntity] = Field(
        description="Infoscience entity found",
        default=None,
    )


def convert_enriched_to_person(enriched: EnrichedAuthor) -> Person:
    """
    Convert an EnrichedAuthor object to a Person object.
    
    This function transforms the agent's working model (EnrichedAuthor) into
    the canonical data model (Person) for storage and output.
    
    Args:
        enriched: EnrichedAuthor object from the agent
        
    Returns:
        Person object with all fields mapped appropriately
    """
    # Prepare emails list
    emails = [enriched.email] if enriched.email else []
    
    # Create Person object with mapped fields
    return Person(
        # Type discriminator
        type="Person",
        
        # Core identity fields
        name=enriched.name,
        email=enriched.email,  # Primary email for backward compatibility
        emails=emails,
        orcidId=enriched.orcidId,
        gitAuthorIds=[],  # Will be set separately based on git author matching
        
        # Affiliation fields
        affiliation=enriched.affiliations or None,  # Deprecated field for backward compatibility
        affiliations=enriched.affiliations,
        currentAffiliation=enriched.currentAffiliation,
        affiliationHistory=enriched.affiliationHistory,
        
        # Additional metadata
        contributionSummary=enriched.contributionSummary,
        biography=enriched.additionalInfo,  # Map additionalInfo to biography
        infoscienceEntity=enriched.infoscienceEntity,
    )


class UserLLMAnalysisResult(BaseModel):
    """Result of user LLM analysis - the structured output from the main user agent"""
    
    relatedToOrganization: Optional[List[str]] = Field(
        description="List of organizations the user is affiliated with",
        default_factory=list,
    )
    relatedToOrganizationJustification: Optional[List[str]] = Field(
        description="Justification for each organization affiliation",
        default_factory=list,
    )
    discipline: Optional[List[Discipline]] = Field(
        description="Scientific disciplines or fields the user works in",
        default_factory=list,
    )
    disciplineJustification: Optional[List[str]] = Field(
        description="Justification for each discipline classification",
        default_factory=list,
    )
    position: Optional[List[str]] = Field(
        description="Professional positions or roles",
        default_factory=list,
    )
    positionJustification: Optional[List[str]] = Field(
        description="Justification for each position",
        default_factory=list,
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


#######################################################
#
#######################################################


class ORCIDEmployment(BaseModel):
    """ORCID employment entry"""

    organization: str = Field(..., description="Organization name")
    role: Optional[str] = Field(None, description="Job title/role")
    start_date: Optional[str] = Field(None, description="Start date")
    end_date: Optional[str] = Field(None, description="End date")
    location: Optional[str] = Field(None, description="Location")
    duration_years: Optional[float] = Field(None, description="Duration in years")


class ORCIDEducation(BaseModel):
    """ORCID education entry"""

    organization: str = Field(..., description="Educational institution")
    degree: Optional[str] = Field(None, description="Degree or qualification")
    start_date: Optional[str] = Field(None, description="Start date")
    end_date: Optional[str] = Field(None, description="End date")
    location: Optional[str] = Field(None, description="Location")
    duration_years: Optional[float] = Field(None, description="Duration in years")


class ORCIDActivities(BaseModel):
    """ORCID activities data"""

    employment: List[ORCIDEmployment] = Field(
        default_factory=list,
        description="Employment history",
    )
    education: List[ORCIDEducation] = Field(
        default_factory=list,
        description="Education history",
    )
    works_count: Optional[int] = Field(None, description="Number of works/publications")
    peer_reviews_count: Optional[int] = Field(
        None,
        description="Number of peer reviews",
    )
    orcid_content: Optional[str] = Field(
        None,
        description="Parsed ORCID Activities content as Markdown",
    )
    orcid_format: Optional[str] = Field(
        default="markdown",
        description="Format of orcid_content",
    )


class GitHubUserMetadata(BaseModel):
    """Pydantic model to store GitHub user metadata with validation"""

    login: str = Field(..., description="GitHub username")
    name: Optional[str] = Field(None, description="User's display name")
    bio: Optional[str] = Field(None, description="User's bio")
    email: Optional[str] = Field(None, description="User's public email")
    location: Optional[str] = Field(None, description="User's location")
    company: Optional[str] = Field(None, description="User's company")
    blog: Optional[str] = Field(None, description="User's blog URL")
    twitter_username: Optional[str] = Field(None, description="Twitter username")
    public_repos: int = Field(..., ge=0, description="Number of public repositories")
    public_gists: int = Field(..., ge=0, description="Number of public gists")
    followers: int = Field(..., ge=0, description="Number of followers")
    following: int = Field(..., ge=0, description="Number of users following")
    created_at: str = Field(..., description="Account creation date")
    updated_at: str = Field(..., description="Last profile update date")
    avatar_url: str = Field(..., description="Avatar image URL")
    html_url: str = Field(..., description="GitHub profile URL")
    orcid: Optional[str] = Field(None, description="ORCID identifier")
    orcid_activities: Optional[ORCIDActivities] = Field(
        None,
        description="ORCID activities data",
    )
    organizations: List[str] = Field(
        default_factory=list,
        description="Public organizations",
    )
    social_accounts: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Social media accounts",
    )
    readme_url: Optional[str] = Field(None, description="Profile README URL if exists")
    readme_content: Optional[str] = Field(
        None,
        description="Profile README content if exists",
    )

    @validator("orcid")
    def validate_orcid(cls, v):
        """Validate ORCID format and convert ID to URL"""
        if v is not None:
            # If it's already a URL, validate and return
            if v.startswith("http"):
                orcid_url_pattern = r"^https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
                if not re.match(orcid_url_pattern, v):
                    raise ValueError(f"Invalid ORCID URL format: {v}")
                return v

            # If it's an ID, validate and convert to URL
            orcid_id_pattern = r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
            if re.match(orcid_id_pattern, v):
                return f"https://orcid.org/{v}"

            raise ValueError(f"Invalid ORCID format: {v}")
        return v

    @validator("email")
    def validate_email(cls, v):
        """Basic email validation"""
        if v is not None and "@" not in v:
            raise ValueError("Invalid email format")
        return v

    class Config:
        """Pydantic configuration"""

        validate_assignment = True
        extra = "forbid"


############################################################
#
############################################################


class GitHubUser(BaseModel):
    name: Optional[str] = None
    fullname: Optional[str] = None
    githubHandle: Optional[str] = None
    githubUserMetadata: Optional[GitHubUserMetadata] = None
    relatedToOrganization: Optional[List[str]] = None
    relatedToOrganizationsROR: Optional[List[Organization]] = None
    relatedToOrganizationJustification: Optional[List[str]] = None
    discipline: Optional[List[Discipline]] = None
    disciplineJustification: Optional[List[str]] = None
    position: Optional[List[str]] = None
    positionJustification: Optional[List[str]] = None
    relatedToEPFL: Optional[bool] = None
    relatedToEPFLJustification: Optional[str] = None
    relatedToEPFLConfidence: Optional[float] = None  # Confidence score (0.0 to 1.0)
    infoscienceEntities: Optional[List[InfoscienceEntity]] = None
