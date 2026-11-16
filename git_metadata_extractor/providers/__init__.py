"""Provider interfaces and mocks for the v2 extraction pipeline."""

from git_metadata_extractor.providers.base import (
    BaseProvider,
    GitHubProvider,
    InfoscienceProvider,
    ORCIDProvider,
    ORCIDRecord,
    ProviderError,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitError,
    RORProvider,
)
from git_metadata_extractor.providers.github_provider import RealGitHubProvider
from git_metadata_extractor.providers.infoscience_provider import RealInfoscienceProvider
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.providers.mock_orcid import MockORCIDProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider
from git_metadata_extractor.providers.orcid_provider import RealORCIDProvider
from git_metadata_extractor.providers.rate_limiter import RateLimiter
from git_metadata_extractor.providers.ror_provider import RealRORProvider

__all__ = [
    "BaseProvider",
    "GitHubProvider",
    "InfoscienceProvider",
    "MockGitHubProvider",
    "MockInfoscienceProvider",
    "MockORCIDProvider",
    "MockRORProvider",
    "ORCIDProvider",
    "ORCIDRecord",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderPermissionError",
    "ProviderRateLimitError",
    "RORProvider",
    "RateLimiter",
    "RealGitHubProvider",
    "RealInfoscienceProvider",
    "RealORCIDProvider",
    "RealRORProvider",
]
