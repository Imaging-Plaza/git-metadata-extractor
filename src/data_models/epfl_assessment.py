"""
EPFL Assessment Data Models

Models for the final EPFL relationship assessment that runs after all enrichments.
"""

from typing import List

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """Individual piece of evidence for EPFL relationship"""
    
    type: str = Field(
        description="Type of evidence (e.g., 'ORCID_EMPLOYMENT', 'EMAIL_DOMAIN', 'LOCATION', 'BIO_MENTION', 'ORGANIZATION_MEMBERSHIP')"
    )
    description: str = Field(
        description="Human-readable description of this evidence"
    )
    confidence_contribution: float = Field(
        description="How much this evidence contributes to confidence (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    source: str = Field(
        description="Where this evidence came from (e.g., 'ORCID', 'GitHub bio', 'README', 'Git authors')"
    )


class EPFLAssessmentResult(BaseModel):
    """Result of final EPFL relationship assessment"""
    
    relatedToEPFL: bool = Field(
        description="Boolean indicating if related to EPFL (true if confidence >= 0.5)"
    )
    relatedToEPFLConfidence: float = Field(
        description="Confidence score (0.0 to 1.0) for EPFL relationship",
        ge=0.0,
        le=1.0,
    )
    relatedToEPFLJustification: str = Field(
        description="Comprehensive justification listing all evidence found"
    )
    evidenceItems: List[EvidenceItem] = Field(
        description="List of all evidence items found and analyzed",
        default_factory=list,
    )

