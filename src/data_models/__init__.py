"""Data models and schemas for the application."""

# __init__.py - Clean up exports
from .api import APIOutput
from .conversion import (
    convert_jsonld_to_pydantic,
    convert_pydantic_to_zod_form_dict,
)
from .infoscience import (
    InfoscienceAuthor,
    InfoscienceLab,
    InfosciencePublication,
    InfoscienceSearchResult,
)
from .models import (
    Discipline,
    Organization,
    Person,
    RepositoryType,
    ResourceType,
)
from .organization import (
    GitHubOrganization,
    GitHubOrganizationMetadata,
    OrganizationAnalysisContext,
    OrganizationEnrichmentResult,
)
from .repository import (
    Commits,
    DataFeed,
    ExecutableNotebook,
    FormalParameter,
    FundingInformation,
    GitAuthor,
    Image,
    ImageKeyword,
    SoftwareImage,
    SoftwareSourceCode,
    debug_field_values,
    # Debugging utilities
    debug_pydantic_validation,
    log_validation_errors,
    validate_repository_data_with_debugging,
)
from .user import (
    EnrichedAuthor,
    GitHubUser,
    GitHubUserMetadata,
    ORCIDActivities,
    ORCIDEducation,
    ORCIDEmployment,
    UserAnalysisContext,
    UserEnrichmentResult,
)

__all__ = [
    # Core models
    "Person",
    "Organization",
    "Discipline",
    "RepositoryType",
    "ResourceType",
    # Repository models
    "SoftwareSourceCode",
    "GitAuthor",
    "Commits",
    "Image",
    "ImageKeyword",
    "FundingInformation",
    "FormalParameter",
    "ExecutableNotebook",
    "SoftwareImage",
    "DataFeed",
    # User models
    "GitHubUser",
    "GitHubUserMetadata",
    "EnrichedAuthor",
    "UserEnrichmentResult",
    "UserAnalysisContext",
    "ORCIDEmployment",
    "ORCIDEducation",
    "ORCIDActivities",
    # Organization models
    "GitHubOrganization",
    "OrganizationEnrichmentResult",
    "OrganizationAnalysisContext",
    "GitHubOrganizationMetadata",
    # Infoscience models
    "InfosciencePublication",
    "InfoscienceAuthor",
    "InfoscienceLab",
    "InfoscienceSearchResult",
    # API models
    "APIOutput",
    # Utilities
    "convert_jsonld_to_pydantic",
    "convert_pydantic_to_zod_form_dict",
    # Debugging utilities
    "debug_pydantic_validation",
    "log_validation_errors",
    "debug_field_values",
    "validate_repository_data_with_debugging",
]
