"""
General data models
"""

from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    List,
    Literal,
    Optional,
)

from pydantic import BaseModel, Field, HttpUrl, field_validator

if TYPE_CHECKING:
    from .academic_catalog import AcademicCatalogRelation


class Person(BaseModel):
    """Person model representing an individual author or contributor"""
    
    # Type discriminator
    type: Literal["Person"] = Field(
        default="Person",
        description="Type discriminator for Person/Organization unions"
    )
    
    # Core identity fields
    name: str = Field(description="Person's name")
    email: Optional[str] = Field(
        description="Primary email address (for backward compatibility)",
        default=None,
    )
    emails: List[str] = Field(
        description="List of all email addresses associated with this person",
        default_factory=list,
    )
    orcidId: Optional[HttpUrl] = Field(
        description="ORCID identifier URL",
        default=None,
    )
    gitAuthorIds: Optional[List[str]] = Field(
        description="List of git author identifiers mapping to this person",
        default_factory=list,
    )
    
    # Affiliation fields
    affiliation: Optional[List[str]] = Field(
        description="List of affiliations (deprecated, use affiliations)",
        default=None,
    )
    affiliations: List[str] = Field(
        description="List of all identified affiliations (current and historical)",
        default_factory=list,
    )
    currentAffiliation: Optional[str] = Field(
        description="Most recent or current affiliation",
        default=None,
    )
    affiliationHistory: List[dict[str, Any]] = Field(
        description="Temporal affiliation information with start/end dates when available",
        default_factory=list,
    )
    
    # Additional metadata
    contributionSummary: Optional[str] = Field(
        description="Summary of the person's contributions to the repository",
        default=None,
    )
    biography: Optional[str] = Field(
        description="Additional biographical or professional information",
        default=None,
    )
    academicCatalogRelations: Optional[List["AcademicCatalogRelation"]] = Field(
        description="Relations to entities in academic catalogs (Infoscience, OpenAlex, EPFL Graph, etc.)",
        default_factory=list,
    )

    @field_validator("orcidId", mode="before")
    @classmethod
    def validate_orcid(cls, v):
        """Convert plain ORCID identifier to full URL if needed."""
        if v is None:
            return v
        if isinstance(v, str):
            # Check if it's a plain ORCID identifier (0000-0000-0000-0000 format)
            if v.startswith("http://") or v.startswith("https://"):
                return v
            # Assume it's a plain identifier, convert to URL
            if len(v) == 19 and v.count("-") == 3:  # ORCID format: 0000-0000-0000-0000
                return f"https://orcid.org/{v}"
        return v


class Organization(BaseModel):
    """Organization model representing an institution or company"""
    
    # Type discriminator
    type: Literal["Organization"] = Field(
        default="Organization",
        description="Type discriminator for Person/Organization unions"
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
            if " " in v or not "." in v:
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
