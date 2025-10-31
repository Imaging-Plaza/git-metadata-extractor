"""
Organization data models
"""

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

from .models import Discipline, Organization, Person
from .repository import GitAuthor, InfoscienceEntity


class OrganizationLLMAnalysisResult(BaseModel):
    """Result of organization LLM analysis - the structured output from the main organization agent"""
    
    organizationType: Optional[str] = Field(
        description="Type of organization (e.g., 'Academic Research Group', 'Industry Company')",
        default=None,
    )
    organizationTypeJustification: Optional[str] = Field(
        description="Justification for the organization type classification",
        default=None,
    )
    description: Optional[str] = Field(
        description="Enhanced description of the organization",
        default=None,
    )
    discipline: Optional[List[Discipline]] = Field(
        description="Scientific/technical disciplines",
        default_factory=list,
    )
    disciplineJustification: Optional[List[str]] = Field(
        description="Justification for each discipline",
        default_factory=list,
    )
    relatedToEPFL: Optional[bool] = Field(
        description="Whether the organization is related to EPFL",
        default=None,
    )
    relatedToEPFLJustification: Optional[str] = Field(
        description="Justification for EPFL relationship",
        default=None,
    )
    relatedToEPFLConfidence: Optional[float] = Field(
        description="Confidence score (0.0-1.0) for EPFL relationship",
        default=None,
        ge=0.0,
        le=1.0,
    )
    infoscienceEntities: Optional[List[InfoscienceEntity]] = Field(
        description="Infoscience entities found for this organization",
        default_factory=list,
    )


class OrganizationEnrichmentResult(BaseModel):
    """Result of organization enrichment analysis"""

    organizations: List[Organization] = Field(
        description="List of all identified organizations with standardized information",
    )
    relatedToEPFL: bool = Field(description="Whether the repository is related to EPFL")
    relatedToEPFLConfidence: float = Field(
        description="Confidence score (0.0 to 1.0) for EPFL relationship",
    )
    relatedToEPFLJustification: str = Field(
        description="Detailed justification for EPFL relationship",
    )


class OrganizationAnalysisContext(BaseModel):
    """Context provided to the agent for analysis"""

    repository_url: str
    git_authors: List[GitAuthor]
    authors: List[Person]
    existing_organizations: List[str]
    existing_justification: Optional[str] = None
    existing_epfl_relation: Optional[bool] = None
    existing_epfl_justification: Optional[str] = None


class GitHubOrganizationMetadata(BaseModel):
    """Pydantic model to store GitHub organization metadata with validation"""

    login: str = Field(..., description="Organization username/login")
    name: Optional[str] = Field(None, description="Organization's display name")
    description: Optional[str] = Field(None, description="Organization's description")
    email: Optional[str] = Field(None, description="Organization's public email")
    location: Optional[str] = Field(None, description="Organization's location")
    company: Optional[str] = Field(None, description="Organization's company")
    blog: Optional[str] = Field(None, description="Organization's blog URL")
    twitter_username: Optional[str] = Field(None, description="Twitter username")
    public_repos: int = Field(..., ge=0, description="Number of public repositories")
    public_gists: int = Field(..., ge=0, description="Number of public gists")
    followers: int = Field(..., ge=0, description="Number of followers")
    following: int = Field(..., ge=0, description="Number of users following")
    created_at: str = Field(..., description="Organization creation date")
    updated_at: str = Field(..., description="Last organization update date")
    avatar_url: str = Field(..., description="Avatar image URL")
    html_url: str = Field(..., description="GitHub organization URL")
    gravatar_id: Optional[str] = Field(None, description="Gravatar ID")
    type: str = Field(..., description="Type (should be 'Organization')")
    node_id: str = Field(..., description="GraphQL node ID")
    url: str = Field(..., description="API URL")
    repos_url: str = Field(..., description="Repositories API URL")
    events_url: str = Field(..., description="Events API URL")
    hooks_url: str = Field(..., description="Hooks API URL")
    issues_url: str = Field(..., description="Issues API URL")
    members_url: str = Field(..., description="Members API URL")

    # Additional metadata
    public_members: List[str] = Field(
        default_factory=list,
        description="Public members",
    )
    repositories: List[str] = Field(
        default_factory=list,
        description="Repository names",
    )
    teams: List[str] = Field(default_factory=list, description="Team names")
    readme_url: Optional[str] = Field(None, description="Profile README URL if exists")
    readme_content: Optional[str] = Field(
        None,
        description="Profile README content if exists",
    )
    social_accounts: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Social media accounts",
    )
    pinned_repositories: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Pinned repositories",
    )

    @validator("email")
    def validate_email(cls, v):
        """Basic email validation"""
        if v is not None and v != "" and "@" not in v:
            raise ValueError("Invalid email format")
        return v

    class Config:
        """Pydantic configuration"""

        validate_assignment = True
        extra = "forbid"


#######################################################
#
#######################################################


class GitHubOrganization(BaseModel):
    name: Optional[str] = None
    organizationType: Optional[str] = None
    githubOrganizationMetadata: Optional[GitHubOrganizationMetadata] = None
    relatedToOrganizationsROR: Optional[List[Organization]] = None
    organizationTypeJustification: Optional[str] = None
    description: Optional[str] = None
    relatedToOrganization: Optional[List[str]] = None
    relatedToOrganizationJustification: Optional[List[str]] = None
    discipline: Optional[List[Discipline]] = None
    disciplineJustification: Optional[List[str]] = None
    relatedToEPFL: Optional[bool] = None
    relatedToEPFLJustification: Optional[str] = None
    relatedToEPFLConfidence: Optional[float] = None  # Confidence score (0.0 to 1.0)
    infoscienceEntities: Optional[List[InfoscienceEntity]] = None
