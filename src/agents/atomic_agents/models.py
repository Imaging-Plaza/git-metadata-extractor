"""
Simplified data models for atomic agents.

These models use only primitive types (strings, numbers, lists, dicts)
to be compatible with LLM agents that don't support complex Pydantic types.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
