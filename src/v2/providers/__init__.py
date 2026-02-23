"""Provider interfaces and mocks for the v2 extraction pipeline."""

from src.v2.providers.base import (
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
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_orcid import MockORCIDProvider
from src.v2.providers.mock_ror import MockRORProvider

__all__ = [
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
]
