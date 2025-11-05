"""
Validation Data Models

Pydantic models for URL validation results.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    """Result of URL validation for ROR or Infoscience entities."""

    is_valid: bool = Field(
        description="Whether the URL matches the expected entity",
    )
    confidence: float = Field(
        description="Confidence score (0.0-1.0) in the validation match",
        ge=0.0,
        le=1.0,
    )
    justification: str = Field(
        description="Explanation of the validation decision",
    )
    matched_fields: List[str] = Field(
        description="Fields that matched between expected and actual entity (name, country, etc.)",
        default_factory=list,
    )
    normalized_url: Optional[str] = Field(
        description="Normalized URL if the original URL was changed/fixed",
        default=None,
    )
    validation_errors: List[str] = Field(
        description="Any errors encountered during validation",
        default_factory=list,
    )

