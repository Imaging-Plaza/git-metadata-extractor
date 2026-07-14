"""
General data models
"""

import hashlib
from enum import Enum
from typing import (
    List,
    Literal,
    Optional,
)

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from .linked_entities import linkedEntitiesRelation


class Affiliation(BaseModel):
    """Structured affiliation with provenance tracking"""

    name: str = Field(
        description="Organization name (e.g., 'Swiss Data Science Center', 'EPFL')",
    )
    organizationId: Optional[str] = Field(
        default=None,
        description="Organization identifier: ROR ID, GitHub handle, or internal ID",
    )
    source: str = Field(
        description="Data source: 'gimie', 'orcid', 'agent_org_enrichment', 'agent_user_enrichment', 'github_profile', 'email_domain'",
    )


class Person(BaseModel):
    """Person model representing an individual author or contributor"""

    id: str = Field(
        default="",
        description="Unique identifier for the person. Link to the person's URL or internal ID",
    )
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
    githubId: Optional[str] = Field(
        description="GitHub username/handle (e.g., 'octocat')",
        default=None,
    )
    orcid: Optional[str] = Field(
        description="ORCID identifier (format: 0000-0000-0000-0000).",
        default=None,
    )
    # gitAuthorIds: Optional[List[str]] = Field(
    #     description="List of git author identifiers mapping to this person",
    #     default_factory=list,
    # )

    # Affiliation fields
    affiliations: List[Affiliation] = Field(
        description="List of current affiliations with provenance tracking",
        default_factory=list,
    )
    affiliationHistory: List[str] = Field(
        description="Temporal affiliation information with start/end dates when available",
        default_factory=list,
    )

    # Provenance tracking
    source: Optional[str] = Field(
        default=None,
        description="Data source: 'gimie', 'llm', 'orcid', 'agent_user_enrichment', 'github_profile'",
    )

    # Additional metadata
    linkedEntities: Optional[List["linkedEntitiesRelation"]] = Field(
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

    id: str = Field(
        default="",
        description="Unique identifier for the organization. Link to the organization's URL or internal ID",
    )
    # Type discriminator
    type: Literal["Organization"] = Field(
        default="Organization",
        description="Type discriminator for Person/Organization unions",
    )

    legalName: Optional[str] = None
    hasRorId: Optional[HttpUrl] = None
    organizationType: Optional[
        str
    ] = None  # university, research institute, lab, department, company, etc.
    attributionConfidence: Optional[float] = None  # Confidence score (0.0 to 1.0)

    # Provenance tracking
    source: Optional[str] = Field(
        default=None,
        description="Data source: 'gimie', 'llm', 'agent_org_enrichment', 'github_profile'",
    )

    linkedEntities: Optional[List["linkedEntitiesRelation"]] = Field(
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
    WEBPAGE = "webpage"
    OTHER = "other"


class ResourceType(str, Enum):
    REPOSITORY = "repository"
    USER = "user"
    ORGANIZATION = "organization"
