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
from .validation import ValidationResult

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
    # Validation models
    "ValidationResult",
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
from typing import Any, Dict, List, Optional, Union

_module = sys.modules[Person.__module__]
_module.AcademicCatalogRelation = AcademicCatalogRelation
_module.InfoscienceEntity = (
    InfoscienceEntity  # Keep for backward compatibility during migration
)
# Add typing imports to namespace for forward reference evaluation
_module.List = List
_module.Dict = Dict
_module.Optional = Optional
_module.Union = Union
_module.Any = Any
Person.model_rebuild()
Organization.model_rebuild()
# Clean up namespace
delattr(_module, "AcademicCatalogRelation")
delattr(_module, "InfoscienceEntity")
delattr(_module, "List")
delattr(_module, "Dict")
delattr(_module, "Optional")
delattr(_module, "Union")
delattr(_module, "Any")

# Rebuild SoftwareSourceCode to resolve AcademicCatalogRelation forward reference
_repo_module = sys.modules[SoftwareSourceCode.__module__]
_repo_module.AcademicCatalogRelation = AcademicCatalogRelation
_repo_module.List = List
_repo_module.Dict = Dict
_repo_module.Optional = Optional
_repo_module.Union = Union
_repo_module.Any = Any
SoftwareSourceCode.model_rebuild()
delattr(_repo_module, "AcademicCatalogRelation")
delattr(_repo_module, "List")
delattr(_repo_module, "Dict")
delattr(_repo_module, "Optional")
delattr(_repo_module, "Union")
delattr(_repo_module, "Any")

# Rebuild EnrichedAuthor to resolve AcademicCatalogRelation forward reference
_user_module = sys.modules[EnrichedAuthor.__module__]
_user_module.AcademicCatalogRelation = AcademicCatalogRelation
_user_module.List = List
_user_module.Dict = Dict
_user_module.Optional = Optional
_user_module.Union = Union
_user_module.Any = Any
EnrichedAuthor.model_rebuild()
delattr(_user_module, "AcademicCatalogRelation")
delattr(_user_module, "List")
delattr(_user_module, "Dict")
delattr(_user_module, "Optional")
delattr(_user_module, "Union")
delattr(_user_module, "Any")

# Rebuild GitHubUser to resolve AcademicCatalogRelation forward reference
_githubuser_module = sys.modules[GitHubUser.__module__]
_githubuser_module.AcademicCatalogRelation = AcademicCatalogRelation
_githubuser_module.List = List
_githubuser_module.Dict = Dict
_githubuser_module.Optional = Optional
_githubuser_module.Union = Union
_githubuser_module.Any = Any
GitHubUser.model_rebuild()
delattr(_githubuser_module, "AcademicCatalogRelation")
delattr(_githubuser_module, "List")
delattr(_githubuser_module, "Dict")
delattr(_githubuser_module, "Optional")
delattr(_githubuser_module, "Union")
delattr(_githubuser_module, "Any")

# Rebuild GitHubOrganization to resolve AcademicCatalogRelation forward reference
_org_module = sys.modules[GitHubOrganization.__module__]
_org_module.AcademicCatalogRelation = AcademicCatalogRelation
_org_module.List = List
_org_module.Dict = Dict
_org_module.Optional = Optional
_org_module.Union = Union
_org_module.Any = Any
GitHubOrganization.model_rebuild()
delattr(_org_module, "AcademicCatalogRelation")
delattr(_org_module, "List")
delattr(_org_module, "Dict")
delattr(_org_module, "Optional")
delattr(_org_module, "Union")
delattr(_org_module, "Any")

# Rebuild OrganizationLLMAnalysisResult to resolve AcademicCatalogRelation forward reference
_orgllm_module = sys.modules[OrganizationLLMAnalysisResult.__module__]
_orgllm_module.AcademicCatalogRelation = AcademicCatalogRelation
_orgllm_module.List = List
_orgllm_module.Dict = Dict
_orgllm_module.Optional = Optional
_orgllm_module.Union = Union
_orgllm_module.Any = Any
OrganizationLLMAnalysisResult.model_rebuild()
delattr(_orgllm_module, "AcademicCatalogRelation")
delattr(_orgllm_module, "List")
delattr(_orgllm_module, "Dict")
delattr(_orgllm_module, "Optional")
delattr(_orgllm_module, "Union")
delattr(_orgllm_module, "Any")
