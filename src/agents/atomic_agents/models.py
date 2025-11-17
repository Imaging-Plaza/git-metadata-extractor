"""
Simplified data models for atomic agents.

These models use only primitive types (strings, numbers, lists, dicts)
to be compatible with LLM agents that don't support complex Pydantic types.
"""

from typing import Any, Dict, List, Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator

# Import existing enums to avoid duplication
from ...data_models.models import Discipline, RepositoryType


class SimplifiedAuthor(BaseModel):
    """Simplified author model with only primitive types."""

    name: str
    email: Optional[str] = None
    orcid: Optional[str] = None
    affiliations: List[str] = Field(default_factory=list)


class SimplifiedGitAuthor(BaseModel):
    """Simplified git author model."""

    name: str
    email: Optional[str] = None
    commits: Optional[Dict[str, Any]] = None


class SimplifiedRepositoryOutput(BaseModel):
    """Simplified repository output model for structured output agent."""

    name: Optional[str] = None
    applicationCategory: Optional[List[str]] = None
    codeRepository: Optional[List[str]] = None  # URLs as strings
    dateCreated: Optional[str] = None  # ISO date string
    license: Optional[str] = None
    author: Optional[List[SimplifiedAuthor]] = None
    gitAuthors: Optional[List[SimplifiedGitAuthor]] = None
    discipline: Optional[List[str]] = None
    disciplineJustification: Optional[List[str]] = None
    repositoryType: str  # Required
    repositoryTypeJustification: List[str]  # Required


class CompiledContext(BaseModel):
    """Compiled context from the context compiler agent."""

    markdown_content: str = Field(
        description="Compiled markdown content with all repository information",
    )
    repository_url: str = Field(description="Repository URL")
    summary: Optional[str] = Field(
        default=None,
        description="Brief summary of the repository",
    )


class EPFLAssessment(BaseModel):
    """EPFL relationship assessment."""

    relatedToEPFL: bool = Field(description="Whether the repository is related to EPFL")
    relatedToEPFLConfidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score (0.0 to 1.0) for EPFL relationship",
    )
    relatedToEPFLJustification: str = Field(
        description="Justification for EPFL relationship assessment",
    )


class EnrichedDataContext(BaseModel):
    """Compiled enriched data context for EPFL final assessment."""

    markdown_content: str = Field(
        description="Compiled markdown content with all enriched repository information",
    )
    repository_url: str = Field(description="Repository URL")
    summary: Optional[str] = Field(
        default=None,
        description="Brief summary of enriched data",
    )


class LinkedEntitiesContext(BaseModel):
    """Compiled academic catalog search results context."""

    markdown_content: str = Field(
        description="Compiled markdown content with search results from academic catalogs",
    )
    repository_name: str = Field(
        description="Repository or tool name that was searched",
    )
    author_names: List[str] = Field(
        description="List of author names that were searched",
        default_factory=list,
    )


# Extract valid values from existing enums (avoiding duplication)
# Note: Literal types must be defined at module level for Pydantic schema generation.
# These values are manually synchronized with Discipline and RepositoryType enums
# from data_models.models to ensure they match exactly.

ValidDiscipline = Literal[
    "Social sciences",
    "Anthropology",
    "Communication studies",
    "Education",
    "Linguistics",
    "Research",
    "Sociology",
    "Geography",
    "Psychology",
    "Politics",
    "Economics",
    "Applied sciences",
    "Health sciences",
    "Electrical engineering",
    "Chemical engineering",
    "Civil engineering",
    "Architecture",
    "Computer engineering",
    "Energy engineering",
    "Military science",
    "Industrial and production engineering",
    "Mechanical engineering",
    "Biological engineering",
    "Environmental science",
    "Systems science and engineering",
    "Information engineering",
    "Agricultural and food sciences",
    "Business",
    "Humanities",
    "History",
    "Literature",
    "Art",
    "Religion",
    "Philosophy",
    "Law",
    "Formal sciences",
    "Mathematics",
    "Logic",
    "Statistics",
    "Theoretical computer science",
    "Natural sciences",
    "Physics",
    "Astronomy",
    "Biology",
    "Chemistry",
    "Earth science",
]

ValidRepositoryType = Literal[
    "software",
    "educational resource",
    "documentation",
    "data",
    "webpage",
    "other",
]

# Runtime verification to ensure Literal values match enum values
_discipline_values = {d.value for d in Discipline}
_literal_discipline_values = get_args(ValidDiscipline)
assert set(_literal_discipline_values) == _discipline_values, (
    f"ValidDiscipline Literal values don't match Discipline enum values. "
    f"Missing: {_discipline_values - set(_literal_discipline_values)}, "
    f"Extra: {set(_literal_discipline_values) - _discipline_values}"
)

_repo_type_values = {rt.value for rt in RepositoryType}
_literal_repo_type_values = get_args(ValidRepositoryType)
assert set(_literal_repo_type_values) == _repo_type_values, (
    f"ValidRepositoryType Literal values don't match RepositoryType enum values. "
    f"Missing: {_repo_type_values - set(_literal_repo_type_values)}, "
    f"Extra: {set(_literal_repo_type_values) - _repo_type_values}"
)


class RepositoryClassification(BaseModel):
    """Repository type and discipline classification."""

    repositoryType: ValidRepositoryType = Field(
        description="Type of repository - must be one of the predefined types",
    )
    repositoryTypeJustification: List[str] = Field(
        description="List of justifications for the repository type classification",
        default_factory=list,
    )
    discipline: List[ValidDiscipline] = Field(
        description="List of scientific disciplines - REQUIRED, must have at least one from the predefined list",
    )
    disciplineJustification: List[str] = Field(
        description="List of justifications for each discipline classification",
        default_factory=list,
    )

    @field_validator("discipline")
    @classmethod
    def validate_discipline_not_empty(cls, v):
        """Ensure at least one discipline is provided."""
        if not v or len(v) == 0:
            raise ValueError(
                "At least one discipline must be provided. Repository must belong to at least one scientific field.",
            )
        return v


class SimplifiedOrganization(BaseModel):
    """Simplified organization model for organization identifier agent."""

    name: str = Field(
        description="Name of the organization",
    )
    organizationType: str = Field(
        description="Type of organization (e.g., 'Research Institute', 'University', 'Company', 'Community Space', 'Non-Profit Organization', 'Government Agency', 'Software Project', 'Research Infrastructure') - REQUIRED",
    )
    id: Optional[str] = Field(
        default=None,
        description="Organization identifier (GitHub URL, website, etc.)",
    )
    attributionConfidence: Optional[float] = Field(
        default=None,
        description="Confidence score (0.0 to 1.0) for the organization's relationship to the repository",
        ge=0.0,
        le=1.0,
    )


class OrganizationIdentification(BaseModel):
    """Identified organizations and their relationships to the repository."""

    relatedToOrganizations: List[SimplifiedOrganization] = Field(
        description="List of organizations related to this repository",
        default_factory=list,
    )
    relatedToOrganizationJustification: List[str] = Field(
        description="List of justifications explaining how each organization is related to the repository",
        default_factory=list,
    )


# Note: SimplifiedLinkedEntitiesRelation and SimplifiedLinkedEntitiesResult
# are now generated dynamically in linked_entities_searcher.py using create_simplified_model()
# to maintain consistency with other atomic agents
