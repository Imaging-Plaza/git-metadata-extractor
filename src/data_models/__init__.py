"""Data models and schemas for the application."""

# __init__.py - Clean up exports
from .academic_catalog import (
    AcademicCatalogEnrichmentResult,
    AcademicCatalogRelation,
    CatalogType,
    EntityType,
)
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
    "InfoscienceEntity",  # Deprecated - use AcademicCatalogRelation
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
    # Academic Catalog models
    "AcademicCatalogRelation",
    "AcademicCatalogEnrichmentResult",
    "CatalogType",
    "EntityType",
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
# This must happen after AcademicCatalogRelation is imported
import sys

_module = sys.modules[Person.__module__]
_module.AcademicCatalogRelation = AcademicCatalogRelation
_module.InfoscienceEntity = (
    InfoscienceEntity  # Keep for backward compatibility during migration
)
Person.model_rebuild()
Organization.model_rebuild()
# Clean up namespace
delattr(_module, "AcademicCatalogRelation")
delattr(_module, "InfoscienceEntity")

# Rebuild SoftwareSourceCode to resolve AcademicCatalogRelation forward reference
_repo_module = sys.modules[SoftwareSourceCode.__module__]
_repo_module.AcademicCatalogRelation = AcademicCatalogRelation
SoftwareSourceCode.model_rebuild()
delattr(_repo_module, "AcademicCatalogRelation")

# Rebuild EnrichedAuthor to resolve AcademicCatalogRelation forward reference
_user_module = sys.modules[EnrichedAuthor.__module__]
_user_module.AcademicCatalogRelation = AcademicCatalogRelation
EnrichedAuthor.model_rebuild()
delattr(_user_module, "AcademicCatalogRelation")

# Rebuild GitHubUser to resolve AcademicCatalogRelation forward reference
_githubuser_module = sys.modules[GitHubUser.__module__]
_githubuser_module.AcademicCatalogRelation = AcademicCatalogRelation
GitHubUser.model_rebuild()
delattr(_githubuser_module, "AcademicCatalogRelation")

# Rebuild GitHubOrganization to resolve AcademicCatalogRelation forward reference
_org_module = sys.modules[GitHubOrganization.__module__]
_org_module.AcademicCatalogRelation = AcademicCatalogRelation
GitHubOrganization.model_rebuild()
delattr(_org_module, "AcademicCatalogRelation")

# Rebuild OrganizationLLMAnalysisResult to resolve AcademicCatalogRelation forward reference
_orgllm_module = sys.modules[OrganizationLLMAnalysisResult.__module__]
_orgllm_module.AcademicCatalogRelation = AcademicCatalogRelation
OrganizationLLMAnalysisResult.model_rebuild()
delattr(_orgllm_module, "AcademicCatalogRelation")
