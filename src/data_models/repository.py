"""
Repository data models
"""

from __future__ import annotations

import hashlib
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
    from .linked_entities import linkedEntitiesRelation

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
    id: Optional[str] = Field(
        default="",
        description="SHA-256 hash of email and name combination",
    )
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

    @model_validator(mode="after")
    def compute_id(self):
        """Compute id as SHA-256 hash of email and name combination."""
        email = self.email or ""
        name = self.name or ""
        emailname = f"{email}{name}".encode()
        self.id = hashlib.sha256(emailname).hexdigest()
        return self

    def anonymize_email_local_part(self, hash_length: int = 12) -> None:
        """
        Replace the local part of the email with a SHA-256 hash while keeping the domain.

        Args:
            hash_length: Number of hexadecimal characters to keep from the hash. Defaults to 12.
        """
        if not self.email or "@" not in self.email:
            return

        local_part, domain = self.email.split("@", 1)
        if not domain:
            return

        hashed_local = hashlib.sha256(local_part.encode("utf-8")).hexdigest()
        if hash_length > 0:
            hashed_local = hashed_local[:hash_length]

        self.email = f"{hashed_local}@{domain}"


class InfoscienceEntity(BaseModel):
    """
    DEPRECATED: Use linkedEntitiesRelation instead.

    Kept temporarily for backward compatibility during migration.
    """

    name: str
    url: HttpUrl
    confidence: float
    justification: str


class SoftwareSourceCode(BaseModel):
    id: str = Field(
        default="",
        description="Unique identifier for the repository. Link to the repository URL.",
    )
    name: Optional[str] = Field(
        default=None,
        description="Repository name",
    )
    applicationCategory: Optional[list[str]] = Field(
        default=None,
        description="Application categories",
    )
    citation: Optional[list[HttpUrl]] = Field(
        default=[],
        description="Citations or references to related publications",
    )
    codeRepository: Optional[list[HttpUrl]] = Field(
        default=[],
        description="Repository URLs",
    )
    keywords: Optional[list[str]] = Field(
        default=[],
        description="Keywords or tags related to the software",
    )
    # conditionsOfAccess: Optional[str] = Field(
    #     default=None,
    #     description="Conditions of access to the repository",
    # )
    dateCreated: Optional[date] = Field(
        default=None,
        description="Creation date in ISO format (YYYY-MM-DD)",
    )
    datePublished: Optional[date] = Field(
        default=None,
        description="Publication date in ISO format (YYYY-MM-DD)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Repository description or summary",
    )
    featureList: Optional[list[str]] = Field(
        default=None,
        description="List of features or capabilities",
    )
    # image: Optional[list[Image]] = Field(
    #     default=None,
    #     description="Images or screenshots of the software",
    # )
    # isAccessibleForFree: Optional[bool] = Field(
    #     default=None,
    #     description="Whether the software is accessible for free",
    # )
    # isBasedOn: Optional[HttpUrl] = Field(
    #     default=None,
    #     description="URL of the software this is based on",
    # )
    # isPluginModuleOf: Optional[list[str]] = Field(
    #     default=None,
    #     description="List of software this is a plugin or module of",
    # )
    license: Optional[str] = Field(
        default=None,
        description="License identifier (e.g., Apache-2.0, MIT)",
    )
    author: Optional[list[Union[Person, Organization]]] = Field(
        default=None,
        description="List of authors/contributors",
    )
    # operatingSystem: Optional[list[str]] = Field(
    #     default=None,
    #     description="Supported operating systems",
    # )
    programmingLanguage: Optional[list[str]] = Field(
        default=None,
        description="Programming languages used in the repository",
    )
    # softwareRequirements: Optional[list[str]] = Field(
    #     default=None,
    #     description="Software dependencies or requirements",
    # )
    # processorRequirements: Optional[list[str]] = Field(
    #     default=None,
    #     description="Processor or CPU requirements",
    # )
    # memoryRequirements: Optional[int] = Field(
    #     default=None,
    #     description="Memory requirements in bytes or MB",
    # )
    # requiresGPU: Optional[bool] = Field(
    #     default=None,
    #     description="Whether the software requires a GPU",
    # )
    # supportingData: Optional[list[DataFeed]] = Field(
    #     default=[],
    #     description="Supporting data feeds or datasets",
    # )
    url: Optional[HttpUrl] = Field(
        default=None,
        description="Primary URL of the repository or project",
    )
    # identifier: Optional[str] = Field(
    #     default=None,
    #     description="Unique identifier for the repository",
    # )
    # hasAcknowledgements: Optional[str] = Field(
    #     default=None,
    #     description="Acknowledgements or credits",
    # )
    # hasDocumentation: Optional[HttpUrl] = Field(
    #     default=None,
    #     description="URL to documentation",
    # )
    # hasExecutableInstructions: Optional[str] = Field(
    #     default=None,
    #     description="Executable instructions or installation guide",
    # )
    hasExecutableNotebook: Optional[list[ExecutableNotebook]] = Field(
        default=[],
        description="Executable notebooks (Jupyter, etc.) in the repository",
    )
    readme: Optional[HttpUrl] = Field(
        default=None,
        description="URL to the README file",
    )
    # hasFunding: Optional[list[FundingInformation]] = Field(
    #     default=None,
    #     description="Funding information and sources",
    # )
    # hasSoftwareImage: Optional[list[SoftwareImage]] = Field(
    #     default=[],
    #     description="Software container images or Docker images",
    # )
    # imagingModality: Optional[list[str]] = Field(
    #     default=None,
    #     description="Imaging modalities supported (for imaging software)",
    # )
    discipline: Optional[list[Discipline]] = Field(
        default=None,
        description="Scientific disciplines",
    )
    disciplineJustification: Optional[list[str]] = Field(
        default=None,
        description="Justification for each discipline",
    )
    relatedDatasets: Optional[list[str]] = Field(
        default=None,
        description="Related datasets or data sources",
    )
    relatedPublications: Optional[list[str]] = Field(
        default=None,
        description="Related publications or papers",
    )
    relatedModels: Optional[list[str]] = Field(
        default=None,
        description="Related models or algorithms",
    )
    relatedAPIs: Optional[list[str]] = Field(
        default=None,
        description="Related APIs or services",
    )
    relatedToOrganizations: Optional[list[Union[str, Organization]]] = Field(
        default=None,
        description="Organizations related to the repository (hosting, funding, affiliation)",
    )
    relatedToOrganizationJustification: Optional[list[str]] = Field(
        default=None,
        description="Justification for each organization relationship",
    )
    repositoryType: RepositoryType = Field(
        description="Repository type",
    )
    repositoryTypeJustification: list[str] = Field(
        description="Justification for repository type",
    )
    relatedToEPFL: Optional[bool] = Field(
        default=None,
        description="Whether the repository is related to EPFL",
    )
    relatedToEPFLConfidence: Optional[float] = Field(
        default=None,
        description="Confidence score (0.0 to 1.0) for EPFL relationship",
    )
    relatedToEPFLJustification: Optional[str] = Field(
        default=None,
        description="Justification for EPFL relationship assessment",
    )
    gitAuthors: Optional[list[GitAuthor]] = Field(
        default=None,
        description="Git commit authors",
    )
    linkedEntities: Optional[list[linkedEntitiesRelation]] = Field(
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
                # Handle both dicts and Pydantic model instances
                author_dict = None
                if isinstance(author, dict):
                    author_dict = author
                elif hasattr(author, "model_dump"):
                    # Pydantic model instance - convert to dict
                    author_dict = author.model_dump()
                elif hasattr(author, "name") or hasattr(author, "legalName"):
                    # Pydantic model instance without model_dump - try to access attributes
                    if hasattr(author, "name"):
                        author_dict = {"name": author.name}
                        # Copy other Person fields if available
                        for field in [
                            "orcid",
                            "emails",
                            "affiliations",
                            "currentAffiliation",
                        ]:
                            if hasattr(author, field):
                                author_dict[field] = getattr(author, field)
                    elif hasattr(author, "legalName"):
                        author_dict = {"legalName": author.legalName}
                        # Copy other Organization fields if available
                        for field in ["hasRorId", "country", "website"]:
                            if hasattr(author, field):
                                author_dict[field] = getattr(author, field)

                if author_dict:
                    # Check if it's a Person/EnrichedAuthor (has "name") or Organization (has "legalName")
                    name = author_dict.get("name")
                    legal_name = author_dict.get("legalName")

                    if name:
                        # Person or EnrichedAuthor object
                        # Check if it has enrichment fields to distinguish
                        has_enrichment = any(
                            k in author_dict
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
                            value is not None for value in author_dict.values()
                        )

                        if has_any_value:
                            # Has some data but missing name/legalName - this is a problem
                            missing_names.append(f"Author {i+1}")
                            logger.warning(
                                f"  ⚠️ Author {i+1} missing name/legalName: {author_dict}",
                            )
                        else:
                            # Completely empty entry - will be filtered out later, no need to warn
                            logger.debug(
                                f"  🔕 Author {i+1} is completely empty (will be filtered)",
                            )
                else:
                    # Not a dict and not a recognizable Pydantic model - keep as-is
                    # Pydantic will handle validation
                    logger.debug(
                        f"  📦 Author {i+1} is a Pydantic model instance: {type(author)}",
                    )
                    valid_authors.append(str(type(author).__name__))

            # Summary
            if missing_names:
                logger.warning(
                    f"  🚨 {len(missing_names)} authors missing names: {', '.join(missing_names)}",
                )
            else:
                logger.debug(f"  ✅ All {len(valid_authors)} authors have names")

            # Filter out completely empty entries (all fields are None)
            # Also convert Pydantic model instances to dicts for consistency
            if v:
                cleaned_authors = []
                for author in v:
                    # Convert Pydantic model instances to dicts
                    if hasattr(author, "model_dump"):
                        author_dict = author.model_dump()
                        # Check if the entry has any non-None values
                        has_any_value = any(
                            value is not None for value in author_dict.values()
                        )
                        if has_any_value:
                            cleaned_authors.append(author_dict)
                        else:
                            logger.debug(
                                "  🗑️ Removing empty author entry (all None values)",
                            )
                    elif isinstance(author, dict):
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
                        # Pydantic model instance without model_dump - keep as-is
                        # Pydantic will handle it
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
                    author_id = author.get("id", "No ID")
                    commits = author.get("commits", {})
                    total_commits = (
                        commits.get("total", 0) if isinstance(commits, dict) else 0
                    )
                    logger.debug(
                        f"    [{i+1}] {name} ({email}) [id: {author_id}] - {total_commits} commits",
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

    def to_simplified_schema(self) -> dict:
        """
        Convert selected SoftwareSourceCode fields to a simplified JSON schema
        suitable for LLM agents that don't support complex types like HttpUrl or date.

        Only includes the following fields:
        - name
        - applicationCategory
        - codeRepository (converted to strings)
        - dateCreated (converted to string)
        - license
        - author (simplified to basic info)
        - gitAuthors (simplified)
        - discipline (converted to strings)
        - repositoryType (converted to string)
        - disciplineJustification
        - repositoryTypeJustification

        Descriptions are automatically extracted from Field() definitions in the model.

        Returns:
            Dictionary with simplified field definitions and expected types
        """
        # Get field information from the model
        model_fields = self.model_fields

        def get_field_description(field_name: str, default: str = "") -> str:
            """Extract description from Field() definition, with fallback to default."""
            if field_name in model_fields:
                field_info = model_fields[field_name]
                if field_info.description:
                    return field_info.description
            return default

        def get_field_required(field_name: str) -> bool:
            """Check if field is required."""
            if field_name in model_fields:
                field_info = model_fields[field_name]
                return field_info.is_required()
            return False

        schema = {
            "name": {
                "type": "string",
                "description": get_field_description("name", "Repository name"),
                "required": get_field_required("name"),
            },
            "applicationCategory": {
                "type": "array",
                "items": {"type": "string"},
                "description": get_field_description(
                    "applicationCategory",
                    "Application categories",
                ),
                "required": get_field_required("applicationCategory"),
            },
            "codeRepository": {
                "type": "array",
                "items": {"type": "string"},
                "description": get_field_description(
                    "codeRepository",
                    "Repository URLs as strings",
                ),
                "required": get_field_required("codeRepository"),
            },
            "dateCreated": {
                "type": "string",
                "description": get_field_description(
                    "dateCreated",
                    "Creation date in ISO format (YYYY-MM-DD)",
                ),
                "required": get_field_required("dateCreated"),
            },
            "license": {
                "type": "string",
                "description": get_field_description(
                    "license",
                    "License identifier (e.g., Apache-2.0, MIT)",
                ),
                "required": get_field_required("license"),
            },
            "author": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "orcid": {"type": "string"},
                        "affiliations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "description": get_field_description(
                    "author",
                    "List of authors/contributors",
                ),
                "required": get_field_required("author"),
            },
            "gitAuthors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "commits": {
                            "type": "object",
                            "properties": {
                                "count": {"type": "integer"},
                                "firstCommit": {"type": "string"},
                                "lastCommit": {"type": "string"},
                            },
                        },
                    },
                },
                "description": get_field_description(
                    "gitAuthors",
                    "Git commit authors",
                ),
                "required": get_field_required("gitAuthors"),
            },
            "discipline": {
                "type": "array",
                "items": {"type": "string"},
                "description": get_field_description(
                    "discipline",
                    "Scientific disciplines",
                ),
                "required": get_field_required("discipline"),
            },
            "disciplineJustification": {
                "type": "array",
                "items": {"type": "string"},
                "description": get_field_description(
                    "disciplineJustification",
                    "Justification for each discipline",
                ),
                "required": get_field_required("disciplineJustification"),
            },
            "repositoryType": {
                "type": "string",
                "description": get_field_description(
                    "repositoryType",
                    "Repository type",
                ),
                "required": get_field_required("repositoryType"),
            },
            "repositoryTypeJustification": {
                "type": "array",
                "items": {"type": "string"},
                "description": get_field_description(
                    "repositoryTypeJustification",
                    "Justification for repository type",
                ),
                "required": get_field_required("repositoryTypeJustification"),
            },
        }
        return schema

    def to_simplified_dict(self) -> dict:
        """
        Convert this SoftwareSourceCode instance to a simplified dictionary
        with only primitive types (strings, numbers, lists, dicts).

        This is used to provide example data to LLM agents that need to understand
        the expected output format but cannot handle complex Pydantic types.

        Returns:
            Dictionary with simplified field values
        """
        result = {}

        # name
        if self.name is not None:
            result["name"] = self.name

        # applicationCategory
        if self.applicationCategory is not None:
            result["applicationCategory"] = list(self.applicationCategory)

        # codeRepository - convert HttpUrl to strings
        if self.codeRepository is not None:
            result["codeRepository"] = [str(url) for url in self.codeRepository]

        # dateCreated - convert date to string
        if self.dateCreated is not None:
            result["dateCreated"] = self.dateCreated.isoformat()

        # license
        if self.license is not None:
            result["license"] = self.license

        # author - simplify to basic info
        if self.author is not None:
            simplified_authors = []
            for auth in self.author:
                if isinstance(auth, Person):
                    author_dict = {
                        "name": auth.name,
                    }
                    if auth.emails:
                        author_dict["email"] = (
                            auth.emails[0]
                            if isinstance(auth.emails, list)
                            else auth.emails
                        )
                    if auth.orcid:
                        author_dict["orcid"] = auth.orcid
                    if auth.affiliations:
                        # Convert Affiliation objects to simple strings for simplified schema
                        author_dict["affiliations"] = [
                            aff.name if hasattr(aff, "name") else str(aff)
                            for aff in auth.affiliations
                        ]
                    simplified_authors.append(author_dict)
                elif isinstance(auth, dict):
                    # Already a dict, extract basic fields
                    author_dict = {}
                    if "name" in auth:
                        author_dict["name"] = auth["name"]
                    if "email" in auth:
                        author_dict["email"] = auth["email"]
                    if "orcid" in auth:
                        author_dict["orcid"] = auth["orcid"]
                    if "affiliations" in auth:
                        author_dict["affiliations"] = auth["affiliations"]
                    if author_dict:
                        simplified_authors.append(author_dict)
            if simplified_authors:
                result["author"] = simplified_authors

        # gitAuthors - simplify
        if self.gitAuthors is not None:
            simplified_git_authors = []
            for git_author in self.gitAuthors:
                git_dict = {
                    "name": git_author.name,
                }
                if git_author.email:
                    git_dict["email"] = git_author.email
                if git_author.commits:
                    git_dict["commits"] = {
                        "count": git_author.commits.count
                        if git_author.commits.count
                        else 0,
                    }
                    if git_author.commits.firstCommit:
                        git_dict["commits"]["firstCommit"] = (
                            git_author.commits.firstCommit.isoformat()
                            if hasattr(git_author.commits.firstCommit, "isoformat")
                            else str(git_author.commits.firstCommit)
                        )
                    if git_author.commits.lastCommit:
                        git_dict["commits"]["lastCommit"] = (
                            git_author.commits.lastCommit.isoformat()
                            if hasattr(git_author.commits.lastCommit, "isoformat")
                            else str(git_author.commits.lastCommit)
                        )
                simplified_git_authors.append(git_dict)
            if simplified_git_authors:
                result["gitAuthors"] = simplified_git_authors

        # discipline - convert enum to strings
        if self.discipline is not None:
            result["discipline"] = [
                str(d.value) if hasattr(d, "value") else str(d) for d in self.discipline
            ]

        # disciplineJustification
        if self.disciplineJustification is not None:
            result["disciplineJustification"] = list(self.disciplineJustification)

        # repositoryType - convert enum to string
        if self.repositoryType is not None:
            result["repositoryType"] = (
                self.repositoryType.value
                if hasattr(self.repositoryType, "value")
                else str(self.repositoryType)
            )

        # repositoryTypeJustification
        if self.repositoryTypeJustification is not None:
            result["repositoryTypeJustification"] = list(
                self.repositoryTypeJustification,
            )

        return result


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
