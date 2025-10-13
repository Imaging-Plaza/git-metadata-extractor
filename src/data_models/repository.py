"""
Repository data models
"""

import logging
from datetime import date
from enum import Enum
from typing import (
    Any,
    List,
    Optional,
    Union,
)

from pydantic import (
    BaseModel,
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


class SoftwareSourceCode(BaseModel):
    name: Optional[str] = None
    applicationCategory: Optional[List[str]] = None
    citation: Optional[List[HttpUrl]] = []
    codeRepository: Optional[List[HttpUrl]] = []
    conditionsOfAccess: Optional[str] = None
    dateCreated: Optional[date] = None
    datePublished: Optional[date] = None
    description: Optional[str] = None
    featureList: Optional[List[str]] = None
    image: Optional[List[Image]] = None
    isAccessibleForFree: Optional[bool] = None
    isBasedOn: Optional[HttpUrl] = None
    isPluginModuleOf: Optional[List[str]] = None
    license: Optional[Annotated[str, StringConstraints(pattern=r"spdx\.org.*")]] = None
    author: Optional[List[Union[Person, Organization]]] = None
    operatingSystem: Optional[List[str]] = None
    programmingLanguage: Optional[List[str]] = None
    softwareRequirements: Optional[List[str]] = None
    processorRequirements: Optional[List[str]] = None
    memoryRequirements: Optional[int] = None
    requiresGPU: Optional[bool] = None
    supportingData: Optional[List[DataFeed]] = []
    url: Optional[HttpUrl] = None
    identifier: Optional[str] = None
    hasAcknowledgements: Optional[str] = None
    hasDocumentation: Optional[HttpUrl] = None
    hasExecutableInstructions: Optional[str] = None
    hasExecutableNotebook: Optional[List[ExecutableNotebook]] = []
    hasParameter: Optional[List[FormalParameter]] = []
    readme: Optional[HttpUrl] = None
    hasFunding: Optional[List[FundingInformation]] = None
    hasSoftwareImage: Optional[List[SoftwareImage]] = []
    imagingModality: Optional[List[str]] = None
    fairLevel: Optional[str] = None
    graph: Optional[str] = None
    discipline: Optional[List[Discipline]] = None
    disciplineJustification: Optional[List[str]] = None
    relatedDatasets: Optional[List[str]] = None
    relatedPublications: Optional[List[str]] = None
    relatedModels: Optional[List[str]] = None
    relatedAPIs: Optional[List[str]] = None
    relatedToOrganizations: Optional[List[str]] = None
    relatedToOrganizationsROR: Optional[List[Organization]] = None
    relatedToOrganizationJustification: Optional[List[str]] = None
    repositoryType: RepositoryType
    repositoryTypeJustification: List[str]
    relatedToEPFL: Optional[bool] = None
    relatedToEPFLConfidence: Optional[float] = None  # Confidence score (0.0 to 1.0)
    relatedToEPFLJustification: Optional[str] = None
    gitAuthors: Optional[List[GitAuthor]] = None
    webpagesToCheck: Optional[List[HttpUrl]] = None

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
                    name = author.get("name")
                    if not name:
                        missing_names.append(f"Author {i+1}")
                        logger.warning(f"  ⚠️ Author {i+1} missing name: {author}")
                    else:
                        valid_authors.append(name)
                        logger.debug(f"  ✅ Author {i+1}: {name}")
                else:
                    logger.warning(f"  ⚠️ Author {i+1} is not a dict: {type(author)}")

            # Summary
            if missing_names:
                logger.warning(
                    f"  🚨 {len(missing_names)} authors missing names: {', '.join(missing_names)}",
                )
            else:
                logger.debug(f"  ✅ All {len(valid_authors)} authors have names")

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

    @field_validator("hasParameter", mode="before")
    @classmethod
    def validate_has_parameter_with_logging(cls, v):
        logger.debug("🔍 Validating hasParameter field")

        if v is None:
            logger.debug("  📝 hasParameter field is None")
            return []

        if isinstance(v, list):
            logger.debug(f"  📊 hasParameter list has {len(v)} items")

            # Check for dimensionality issues
            dim_issues = []
            valid_params = []

            for i, param in enumerate(v):
                if isinstance(param, dict):
                    name = param.get("name", f"Parameter {i+1}")
                    has_dim = param.get("hasDimensionality")

                    if has_dim is not None:
                        if isinstance(has_dim, int) and has_dim > 0:
                            valid_params.append(f"{name} (dim: {has_dim})")
                            logger.debug(f"  ✅ {name}: dimensionality = {has_dim}")
                        else:
                            dim_issues.append(f"{name} (invalid dim: {has_dim})")
                            logger.warning(
                                f"  ⚠️ {name}: invalid dimensionality = {has_dim} (type: {type(has_dim)})",
                            )
                    else:
                        logger.debug(f"  📝 {name}: no dimensionality specified")
                else:
                    logger.warning(f"  ⚠️ Parameter {i+1} is not a dict: {type(param)}")

            # Summary
            if dim_issues:
                logger.warning(
                    f"  🚨 {len(dim_issues)} parameters with dimensionality issues: {', '.join(dim_issues)}",
                )
            else:
                logger.debug(
                    f"  ✅ All {len(valid_params)} parameters have valid dimensionality",
                )
        else:
            logger.warning(f"  ⚠️ hasParameter field is not a list: {type(v)}")

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
            f"  ⚙️ Parameters: {len(self.hasParameter) if self.hasParameter else 0}",
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
                    org_lower = (
                        org.lower().strip()
                    )  # Convert to lowercase for comparison

                    if org_lower not in seen_lower:
                        unique_orgs.append(org)  # Keep original case
                        seen_lower.add(org_lower)  # Store lowercase version
                    else:
                        logger.debug(
                            f"  🔄 Removed case-insensitive duplicate: '{org}' (matches existing)",
                        )

            logger.debug(f"  📊 Organizations: {len(v)} → {len(unique_orgs)}")
            return unique_orgs

        return v


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
        git_authors: List[Any],
        gimie_output: Optional[Any] = None,
    ):
        self.repo_url = repo_url
        self.git_authors = git_authors
        self.gimie_output = gimie_output
