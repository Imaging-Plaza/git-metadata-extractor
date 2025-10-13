"""
General data models
"""

from enum import Enum
from typing import (
    List,
    Optional,
)

from pydantic import BaseModel, HttpUrl


class Person(BaseModel):
    name: str
    orcidId: Optional[HttpUrl] = None
    affiliation: Optional[List[str]] = None


class Organization(BaseModel):
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
