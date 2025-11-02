"""Data models and schemas for the application."""

# __init__.py - Clean up exports
from .api import APIOutput
from .conversion import (
    convert_jsonld_to_pydantic,
    convert_pydantic_to_zod_form_dict,
)
from .epfl_assessment import EPFLAssessmentResult, EvidenceItem
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
    OrganizationLLMAnalysisResult,
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
    InfoscienceEntity,
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
    UserLLMAnalysisResult,
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
    "InfoscienceEntity",
    "FundingInformation",
    "FormalParameter",
    "ExecutableNotebook",
    "SoftwareImage",
    "DataFeed",
    # User models
    "GitHubUser",
    "GitHubUserMetadata",
    "EnrichedAuthor",
    "UserLLMAnalysisResult",
    "UserEnrichmentResult",
    "UserAnalysisContext",
    "ORCIDEmployment",
    "ORCIDEducation",
    "ORCIDActivities",
    # Organization models
    "GitHubOrganization",
    "OrganizationLLMAnalysisResult",
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
    # EPFL Assessment models
    "EPFLAssessmentResult",
    "EvidenceItem",
    # Utilities
    "convert_jsonld_to_pydantic",
    "convert_pydantic_to_zod_form_dict",
    # Debugging utilities
    "debug_pydantic_validation",
    "log_validation_errors",
    "debug_field_values",
    "validate_repository_data_with_debugging",
]

# Rebuild models after all imports to resolve forward references
# This must happen after InfoscienceEntity is imported from repository
# We need to pass InfoscienceEntity in the namespace for the forward reference to resolve
import sys
_module = sys.modules[Person.__module__]
_module.InfoscienceEntity = InfoscienceEntity
Person.model_rebuild()
Organization.model_rebuild()
# Clean up namespace
delattr(_module, 'InfoscienceEntity')
