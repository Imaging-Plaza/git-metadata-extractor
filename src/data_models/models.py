"""
General data models
"""

import hashlib
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    List,
    Literal,
    Optional,
)

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

if TYPE_CHECKING:
    from .academic_catalog import AcademicCatalogRelation


class Person(BaseModel):
    """Person model representing an individual author or contributor"""

    # Type discriminator
    type: Literal["Person"] = Field(
        default="Person",
        description="Type discriminator for Person/Organization unions",
    )

    # Core identity fields
    name: str = Field(description="Person's name")
    emails: Optional[List[str]] = Field(
        description="Email address(es) - can be a single string or a list of strings",
        default_factory=list,
    )
    orcid: Optional[str] = Field(
        description="ORCID identifier (format: 0000-0000-0000-0000 or https://orcid.org/0000-0000-0000-0000). Examples: '0000-0002-1234-5678', '0000-0000-0000-000X'",
        default=None,
    )
    gitAuthorIds: Optional[List[str]] = Field(
        description="List of git author identifiers mapping to this person",
        default_factory=list,
    )

    # Affiliation fields
    affiliations: List[str] = Field(
        description="List of all currents affiliations",
        default_factory=list,
    )
    affiliationHistory: List[dict[str, Any]] = Field(
        description="Temporal affiliation information with start/end dates when available",
        default_factory=list,
    )

    # Additional metadata
    academicCatalogRelations: Optional[List["AcademicCatalogRelation"]] = Field(
        description="Relations to entities in academic catalogs (Infoscience, OpenAlex, EPFL Graph, etc.)",
        default_factory=list,
    )

    @field_validator("orcid", mode="before")
    @classmethod
    def validate_orcid(cls, v):
        """Validate ORCID format and convert ID to URL if needed."""
        import re

        if v is None:
            return v

        if isinstance(v, str):
            # If it's already a URL, validate and return as-is (store as string)
            if v.startswith("http"):
                orcid_url_pattern = r"^https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
                if not re.match(orcid_url_pattern, v):
                    raise ValueError(f"Invalid ORCID URL format: {v}")
                return v

            # If it's an ID, validate and return as-is (store as plain ID string)
            orcid_id_pattern = r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
            if re.match(orcid_id_pattern, v):
                return v

            raise ValueError(
                f"Invalid ORCID format: {v}. Expected format: 0000-0000-0000-0000 or https://orcid.org/0000-0000-0000-0000",
            )

        return v

    def anonymize_emails(self, hash_length: int = 12) -> None:
        """
        Replace the local part of each email with a SHA-256 hash while keeping the domain.

        Args:
            hash_length: Number of hexadecimal characters to keep from the hash. Defaults to 12.
        """
        if not self.emails:
            return

        anonymized_emails: list[str] = []
        for email in self.emails:
            if not email or "@" not in email:
                anonymized_emails.append(email)
                continue

            local_part, domain = email.split("@", 1)
            if not domain:
                anonymized_emails.append(email)
                continue

            hashed_local = hashlib.sha256(local_part.encode("utf-8")).hexdigest()
            if hash_length > 0:
                hashed_local = hashed_local[:hash_length]

            anonymized_emails.append(f"{hashed_local}@{domain}")

        self.emails = anonymized_emails

    @model_validator(mode="after")
    def anonymize_emails_after_validation(self):
        """
        Automatically anonymize emails after Person model validation to ensure privacy.
        """
        self.anonymize_emails()
        return self


class Organization(BaseModel):
    """Organization model representing an institution or company"""

    # Type discriminator
    type: Literal["Organization"] = Field(
        default="Organization",
        description="Type discriminator for Person/Organization unions",
    )

    legalName: Optional[str] = None
    hasRorId: Optional[HttpUrl] = None
    alternateNames: Optional[
        List[str]
    ] = None  # Other names the organization is known by
    organizationType: Optional[
        str
    ] = None  # university, research institute, lab, department, company, etc.
    parentOrganization: Optional[
        str
    ] = None  # Name of parent organization if applicable
    country: Optional[str] = None  # Country where the organization is located
    website: Optional[HttpUrl] = None  # Official website
    attributionConfidence: Optional[float] = None  # Confidence score (0.0 to 1.0)
    academicCatalogRelations: Optional[List["AcademicCatalogRelation"]] = Field(
        description="Relations to entities in academic catalogs (Infoscience, OpenAlex, EPFL Graph, etc.)",
        default_factory=list,
    )

    @field_validator("hasRorId", mode="before")
    @classmethod
    def validate_ror(cls, v):
        """Convert plain ROR identifier to full URL if needed."""
        if v is None:
            return v
        if isinstance(v, str):
            # Check if it's already a URL
            if v.startswith("http://") or v.startswith("https://"):
                return v
            # Assume it's a plain ROR identifier, convert to URL
            # ROR IDs typically look like: 05gzmn429 or 0abcdef12
            if len(v) == 9:  # ROR format is 9 characters
                return f"https://ror.org/{v}"
        return v

    @field_validator("website", mode="before")
    @classmethod
    def validate_website(cls, v):
        """Ensure website URL is valid, fix common issues."""
        if v is None or v == "":
            return None
        if isinstance(v, str):
            v = v.strip()
            # If it doesn't start with http:// or https://, add https://
            if not v.startswith(("http://", "https://")):
                v = f"https://{v}"
            # Basic validation - if it doesn't look like a URL, return None
            if " " in v or "." not in v:
                return None
        return v


class Discipline(str, Enum):
    SOCIAL_SCIENCES = "Social sciences"
    ANTHROPOLOGY = "Anthropology"
    COMMUNICATION_STUDIES = "Communication studies"
    EDUCATION = "Education"
    LINGUISTICS = "Linguistics"
    RESEARCH = "Research"
    SOCIOLOGY = "Sociology"
    GEOGRAPHY = "Geography"
    PSYCHOLOGY = "Psychology"
    POLITICS = "Politics"
    ECONOMICS = "Economics"
    APPLIED_SCIENCES = "Applied sciences"
    HEALTH_SCIENCES = "Health sciences"
    ELECTRICAL_ENGINEERING = "Electrical engineering"
    CHEMICAL_ENGINEERING = "Chemical engineering"
    CIVIL_ENGINEERING = "Civil engineering"
    ARCHITECTURE = "Architecture"
    COMPUTER_ENGINEERING = "Computer engineering"
    ENERGY_ENGINEERING = "Energy engineering"
    MILITARY_SCIENCE = "Military science"
    INDUSTRIAL_PRODUCTION_ENGINEERING = "Industrial and production engineering"
    MECHANICAL_ENGINEERING = "Mechanical engineering"
    BIOLOGICAL_ENGINEERING = "Biological engineering"
    ENVIRONMENTAL_SCIENCE = "Environmental science"
    SYSTEMS_SCIENCE_ENGINEERING = "Systems science and engineering"
    INFORMATION_ENGINEERING = "Information engineering"
    AGRICULTURAL_FOOD_SCIENCES = "Agricultural and food sciences"
    BUSINESS = "Business"
    HUMANITIES = "Humanities"
    HISTORY = "History"
    LITERATURE = "Literature"
    ART = "Art"
    RELIGION = "Religion"
    PHILOSOPHY = "Philosophy"
    LAW = "Law"
    FORMAL_SCIENCES = "Formal sciences"
    MATHEMATICS = "Mathematics"
    LOGIC = "Logic"
    STATISTICS = "Statistics"
    THEORETICAL_COMPUTER_SCIENCE = "Theoretical computer science"
    NATURAL_SCIENCES = "Natural sciences"
    PHYSICS = "Physics"
    ASTRONOMY = "Astronomy"
    BIOLOGY = "Biology"
    CHEMISTRY = "Chemistry"
    EARTH_SCIENCE = "Earth science"


class RepositoryType(str, Enum):
    SOFTWARE = "software"
    EDUCATIONAL_RESOURCE = "educational resource"
    DOCUMENTATION = "documentation"
    DATA = "data"
    OTHER = "other"


class ResourceType(str, Enum):
    REPOSITORY = "repository"
    USER = "user"
    ORGANIZATION = "organization"
