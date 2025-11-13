"""
Repository data models
"""

from __future__ import annotations

import logging
from datetime import date
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Union,
)

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    StringConstraints,
    ValidationError,
    conint,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated

logger = logging.getLogger(__name__)


from .models import (
    Discipline,
    Organization,
    Person,
    RepositoryType,
)

if TYPE_CHECKING:
    from .academic_catalog import AcademicCatalogRelation

#####################################################################
# Debugging Utilities
#####################################################################


def debug_pydantic_validation(data: dict, model_class, context: str = ""):
    """Comprehensive Pydantic validation debugging"""
    logger.info(f"Starting validation debug {context}")

    # 1. Log input data structure
    logger.debug("Input data structure:")
    for key, value in data.items():
        logger.debug(f"  {key}: {type(value)} = {value}")

    # 2. Try validation and catch detailed errors
    try:
        validated_data = model_class.model_validate(data)
        logger.info("Validation successful")
        return validated_data

    except ValidationError as e:
        logger.error(f"Validation failed with {len(e.errors())} errors:")

        # 3. Log each error in detail
        for i, error in enumerate(e.errors(), 1):
            field_path = " -> ".join(str(loc) for loc in error["loc"])
            logger.error(f"Error {i}:")
            logger.error(f"  Field: {field_path}")
            logger.error(f"  Type: {error['type']}")
            logger.error(f"  Message: {error['msg']}")
            logger.error(f"  Input: {error.get('input')}")
            logger.error(f"  Context: {error.get('ctx', 'None')}")

        # 4. Log the raw error for debugging
        logger.error(f"Raw ValidationError: {e}")

        raise e


def log_validation_errors(validation_error: ValidationError, context: str = ""):
    """Log detailed Pydantic validation errors"""
    logger.error(
        f"Validation failed {context}: {len(validation_error.errors())} errors",
    )

    for i, error in enumerate(validation_error.errors(), 1):
        field_path = " -> ".join(str(loc) for loc in error["loc"])

        logger.error(f"Error {i}:")
        logger.error(f"  Field: {field_path}")
        logger.error(f"  Type: {error['type']}")
        logger.error(f"  Message: {error['msg']}")
        logger.error(f"  Input: {error.get('input', 'N/A')}")

        # Log additional context if available
        if "ctx" in error:
            logger.error(f"  Context: {error['ctx']}")


def debug_field_values(data: dict, model_class):
    """Log field values before validation"""
    logger.debug("Field values before validation:")
    for field_name, field_info in model_class.model_fields.items():
        value = data.get(field_name)
        logger.debug(f"  {field_name}: {value} (type: {type(value)})")

        # Special handling for complex fields
        if isinstance(value, list) and len(value) > 0:
            logger.debug(f"    List items: {len(value)}")
            for i, item in enumerate(value[:3]):  # Show first 3 items
                logger.debug(f"      [{i}]: {item} (type: {type(item)})")
        elif isinstance(value, dict):
            logger.debug(f"    Dict keys: {list(value.keys())}")


#####################################################################
# Properties
#####################################################################


class FundingInformation(BaseModel):
    identifier: Optional[str] = None
    fundingGrant: Optional[str] = None
    fundingSource: Organization


class FormalParameter(BaseModel):
    name: Annotated[str, StringConstraints(max_length=60)]
    description: Optional[Annotated[str, StringConstraints(max_length=2000)]] = None
    encodingFormat: Optional[HttpUrl] = None
    hasDimensionality: Optional[Annotated[int, conint(gt=0)]] = None
    hasFormat: Optional[str] = None
    defaultValue: Optional[str] = None
    valueRequired: Optional[bool] = None

    @field_validator("hasDimensionality", mode="before")
    @classmethod
    def validate_has_dimensionality_with_logging(cls, v):
        logger.debug(f"Validating hasDimensionality field: {v} (type: {type(v)})")

        if v is None:
            logger.debug("hasDimensionality is None - this is allowed")
            return None

        if isinstance(v, int) and v > 0:
            logger.debug(f"hasDimensionality is valid positive integer: {v}")
            return v

        logger.warning(f"hasDimensionality has invalid value: {v} (type: {type(v)})")
        return v


class ExecutableNotebook(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    url: HttpUrl


class SoftwareImage(BaseModel):
    name: str
    description: str
    softwareVersion: Annotated[
        str,
        StringConstraints(pattern=r"[0-9]+\.[0-9]+\.[0-9]+"),
    ]
    availableInRegistry: HttpUrl


class DataFeed(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    contentUrl: Optional[HttpUrl] = None
    measurementTechnique: Optional[str] = None
    variableMeasured: Optional[str] = None


class ImageKeyword(str, Enum):
    LOGO = "logo"
    ILLUSTRATIVE_IMAGE = "illustrative image"
    BEFORE_IMAGE = "before image"
    AFTER_IMAGE = "after image"
    ANIMATED_IMAGE = "animated image"


class Image(BaseModel):
    contentUrl: HttpUrl
    keywords: ImageKeyword = ImageKeyword.ILLUSTRATIVE_IMAGE


class Commits(BaseModel):
    total: int
    firstCommitDate: Optional[date] = None
    lastCommitDate: Optional[date] = None


class GitAuthor(BaseModel):
    name: str
    email: Optional[str] = None
    commits: Optional[Commits] = None

    @field_validator("commits", mode="before")
    @classmethod
    def validate_commits_with_logging(cls, v):
        logger.debug(f"Validating commits field: {v} (type: {type(v)})")

        if v is None:
            logger.debug("commits is None - this is allowed")
            return None

        if isinstance(v, Commits):
            logger.debug(f"commits is already a Commits object: {v}")
            return v

        if isinstance(v, dict):
            logger.debug(f"commits is a dict, will be converted to Commits: {v}")
            return v

        logger.warning(f"commits has unexpected type: {type(v)}")
        return v


class InfoscienceEntity(BaseModel):
    """
    DEPRECATED: Use AcademicCatalogRelation instead.

    Kept temporarily for backward compatibility during migration.
    """

    name: str
    url: HttpUrl
    confidence: float
    justification: str


class SoftwareSourceCode(BaseModel):
    name: Optional[str] = None
    applicationCategory: Optional[list[str]] = None
    citation: Optional[list[HttpUrl]] = []
    codeRepository: Optional[list[HttpUrl]] = []
    conditionsOfAccess: Optional[str] = None
    dateCreated: Optional[date] = None
    datePublished: Optional[date] = None
    description: Optional[str] = None
    featureList: Optional[list[str]] = None
    image: Optional[list[Image]] = None
    isAccessibleForFree: Optional[bool] = None
    isBasedOn: Optional[HttpUrl] = None
    isPluginModuleOf: Optional[list[str]] = None
    license: Optional[Annotated[str, StringConstraints(pattern=r"spdx\.org.*")]] = None
    author: Optional[list[Union[Person, Organization]]] = None
    operatingSystem: Optional[list[str]] = None
    programmingLanguage: Optional[list[str]] = None
    softwareRequirements: Optional[list[str]] = None
    processorRequirements: Optional[list[str]] = None
    memoryRequirements: Optional[int] = None
    requiresGPU: Optional[bool] = None
    supportingData: Optional[list[DataFeed]] = []
    url: Optional[HttpUrl] = None
    identifier: Optional[str] = None
    hasAcknowledgements: Optional[str] = None
    hasDocumentation: Optional[HttpUrl] = None
    hasExecutableInstructions: Optional[str] = None
    hasExecutableNotebook: Optional[List[ExecutableNotebook]] = []
    readme: Optional[HttpUrl] = None
    hasFunding: Optional[List[FundingInformation]] = None
    hasSoftwareImage: Optional[List[SoftwareImage]] = []
    imagingModality: Optional[List[str]] = None
    discipline: Optional[List[Discipline]] = None
    disciplineJustification: Optional[List[str]] = None
    relatedDatasets: Optional[List[str]] = None
    relatedPublications: Optional[List[str]] = None
    relatedModels: Optional[List[str]] = None
    relatedAPIs: Optional[List[str]] = None
    relatedToOrganizations: Optional[List[Union[str, Organization]]] = None
    relatedToOrganizationJustification: Optional[List[str]] = None
    repositoryType: RepositoryType
    repositoryTypeJustification: list[str]
    relatedToEPFL: Optional[bool] = None
    relatedToEPFLConfidence: Optional[float] = None  # Confidence score (0.0 to 1.0)
    relatedToEPFLJustification: Optional[str] = None
    gitAuthors: Optional[List[GitAuthor]] = None
    academicCatalogRelations: Optional[List[AcademicCatalogRelation]] = Field(
        description="Relations to entities in academic catalogs (Infoscience, OpenAlex, EPFL Graph, etc.)",
        default_factory=list,
    )

    @field_validator("author", mode="before")
    @classmethod
    def validate_author_with_logging(cls, v):
        logger.debug("🔍 Validating author field")

        if v is None:
            logger.debug("  📝 Author field is None")
            return None

        if isinstance(v, list):
            logger.debug(f"  📊 Author list has {len(v)} items")

            # Check for missing names
            missing_names = []
            valid_authors = []

            for i, author in enumerate(v):
                if isinstance(author, dict):
                    # Check if it's a Person/EnrichedAuthor (has "name") or Organization (has "legalName")
                    name = author.get("name")
                    legal_name = author.get("legalName")

                    if name:
                        # Person or EnrichedAuthor object
                        # Check if it has enrichment fields to distinguish
                        has_enrichment = any(
                            k in author
                            for k in [
                                "currentAffiliation",
                                "affiliationHistory",
                                "confidenceScore",
                            ]
                        )
                        author_type = "EnrichedAuthor" if has_enrichment else "Person"
                        valid_authors.append(name)
                        logger.debug(f"  ✅ Author {i+1} ({author_type}): {name}")
                    elif legal_name:
                        # Organization object
                        valid_authors.append(legal_name)
                        logger.debug(f"  ✅ Author {i+1} (Organization): {legal_name}")
                    else:
                        # Neither Person nor Organization - check if it's an empty/invalid entry
                        # Check if the entire entry is empty (all None values)
                        has_any_value = any(
                            value is not None for value in author.values()
                        )

                        if has_any_value:
                            # Has some data but missing name/legalName - this is a problem
                            missing_names.append(f"Author {i+1}")
                            logger.warning(
                                f"  ⚠️ Author {i+1} missing name/legalName: {author}",
                            )
                        else:
                            # Completely empty entry - will be filtered out later, no need to warn
                            logger.debug(
                                f"  🔕 Author {i+1} is completely empty (will be filtered)",
                            )
                else:
                    logger.warning(f"  ⚠️ Author {i+1} is not a dict: {type(author)}")

            # Summary
            if missing_names:
                logger.warning(
                    f"  🚨 {len(missing_names)} authors missing names: {', '.join(missing_names)}",
                )
            else:
                logger.debug(f"  ✅ All {len(valid_authors)} authors have names")

            # Filter out completely empty entries (all fields are None)
            if v:
                cleaned_authors = []
                for author in v:
                    if isinstance(author, dict):
                        # Check if the entry has any non-None values
                        has_any_value = any(
                            value is not None for value in author.values()
                        )
                        if has_any_value:
                            cleaned_authors.append(author)
                        else:
                            logger.debug(
                                "  🗑️ Removing empty author entry (all None values)",
                            )
                    else:
                        # Keep non-dict entries (they'll be handled by Pydantic)
                        cleaned_authors.append(author)

                if len(cleaned_authors) != len(v):
                    logger.info(
                        f"  ♻️ Filtered {len(v) - len(cleaned_authors)} empty author entries",
                    )
                    v = cleaned_authors

        else:
            logger.warning(f"  ⚠️ Author field is not a list: {type(v)}")

        return v

    @field_validator("gitAuthors", mode="before")
    @classmethod
    def validate_git_authors_with_logging(cls, v):
        logger.debug("🔍 Validating gitAuthors field")

        if v is None:
            logger.debug("  📝 gitAuthors field is None")
            return None

        if isinstance(v, list):
            logger.debug(f"  📊 gitAuthors list has {len(v)} items")

            # Group by name to show duplicates
            name_counts = {}
            for author in v:
                if isinstance(author, dict):
                    name = author.get("name", "Unknown")
                    name_counts[name] = name_counts.get(name, 0) + 1

            # Show summary
            logger.debug("  👥 Author summary:")
            for name, count in sorted(name_counts.items()):
                logger.debug(f"    • {name}: {count} entry(ies)")

            # Show detailed info for first few authors
            logger.debug("  📋 Detailed author info:")
            for i, author in enumerate(v[:5]):  # Show first 5
                if isinstance(author, dict):
                    name = author.get("name", "Unknown")
                    email = author.get("email", "No email")
                    commits = author.get("commits", {})
                    total_commits = (
                        commits.get("total", 0) if isinstance(commits, dict) else 0
                    )
                    logger.debug(
                        f"    [{i+1}] {name} ({email}) - {total_commits} commits",
                    )

            if len(v) > 5:
                logger.debug(f"    ... and {len(v) - 5} more authors")
        else:
            logger.warning(f"  ⚠️ gitAuthors field is not a list: {type(v)}")

        return v

    @model_validator(mode="after")
    def validate_model_with_logging(self):
        repo_name = getattr(self, "name", "unnamed")
        logger.debug(f"🎉 Model validation completed for '{repo_name}'")

        # Log key field states in a readable format
        logger.debug("📊 Final validation summary:")
        logger.debug(f"  👥 Authors: {len(self.author) if self.author else 0}")
        logger.debug(
            f"  🔧 Git Authors: {len(self.gitAuthors) if self.gitAuthors else 0}",
        )
        logger.debug(
            f"  🏷️ Repository Type: {getattr(self, 'repositoryType', 'Not set')}",
        )

        return self

    @field_validator("relatedToOrganizations", mode="before")
    @classmethod
    def validate_related_to_organizations_with_logging(cls, v):
        logger.debug("🔍 Validating relatedToOrganizations field")

        if v is None:
            return None

        if isinstance(v, list):
            unique_orgs = []
            seen_lower = set()  # Store lowercase versions for comparison

            for org in v:
                if org:
                    # Handle both string and Organization objects
                    if isinstance(org, str):
                        org_lower = org.lower().strip()
                        org_key = org_lower
                        org_value = org  # Keep original case
                    elif isinstance(org, Organization):
                        # Use legalName for Organization objects
                        org_name = org.legalName or ""
                        org_lower = org_name.lower().strip()
                        org_key = org_lower
                        org_value = org
                    elif isinstance(org, dict):
                        # Handle dict representation (could be string or Organization)
                        if "legalName" in org:
                            org_name = org.get("legalName", "")
                            org_lower = org_name.lower().strip()
                            org_key = org_lower
                            org_value = Organization(**org) if org else None
                        else:
                            # Treat as string
                            org_str = str(org)
                            org_lower = org_str.lower().strip()
                            org_key = org_lower
                            org_value = org_str
                    else:
                        # Convert to string for comparison
                        org_str = str(org)
                        org_lower = org_str.lower().strip()
                        org_key = org_lower
                        org_value = org_str

                    if org_value and org_key not in seen_lower:
                        unique_orgs.append(org_value)  # Keep original format
                        seen_lower.add(org_key)  # Store lowercase version
                    else:
                        logger.debug(
                            f"  🔄 Removed case-insensitive duplicate: '{org_key}' (matches existing)",
                        )

            logger.debug(f"  📊 Organizations: {len(v)} → {len(unique_orgs)}")
            return unique_orgs

        return v

    def convert_pydantic_to_jsonld(self) -> dict:
        """
        Convert this SoftwareSourceCode instance to JSON-LD format.

        Returns a JSON-LD graph structure with proper @context, @type,
        and semantic URIs for all fields and nested models.

        Returns:
            Dictionary containing JSON-LD representation
        """
        from .conversion import convert_pydantic_to_jsonld

        # Use codeRepository as base URL if available
        base_url = None
        if self.codeRepository and len(self.codeRepository) > 0:
            base_url = str(self.codeRepository[0])
        elif self.url:
            base_url = str(self.url)

        return convert_pydantic_to_jsonld(self, base_url=base_url)


#####################################################################
# Usage Examples
#####################################################################


def validate_repository_data_with_debugging(data: dict, repo_url: str = ""):
    """
    Example function showing how to use the debugging utilities
    """
    context = f"for repository {repo_url}" if repo_url else ""

    try:
        # Use the comprehensive debugging function
        validated_data = debug_pydantic_validation(data, SoftwareSourceCode, context)
        return validated_data

    except ValidationError as e:
        # Log detailed errors
        log_validation_errors(e, context)

        # Also log field values for debugging
        debug_field_values(data, SoftwareSourceCode)

        raise e


#####################################################################
# Transitions models
#####################################################################


class RepositoryAnalysisContext:
    """Context for repository analysis agent."""

    def __init__(
        self,
        repo_url: str,
        git_authors: list[Any],
        gimie_output: Optional[Any] = None,
    ):
        self.repo_url = repo_url
        self.git_authors = git_authors
        self.gimie_output = gimie_output
