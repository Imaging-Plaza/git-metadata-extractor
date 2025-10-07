from typing import (
    List,
    Optional,
)

from pydantic import BaseModel, Field

from .models import GitAuthor, Organization, Person


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
