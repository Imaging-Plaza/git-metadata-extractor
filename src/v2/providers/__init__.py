"""Provider interfaces and mocks for the v2 extraction pipeline."""

from src.v2.providers.base import (
    GitHubProvider,
    InfoscienceProvider,
    RORProvider,
    ProviderError,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_ror import MockRORProvider

__all__ = [
    "GitHubProvider",
    "InfoscienceProvider",
    "RORProvider",
    "MockGitHubProvider",
    "MockInfoscienceProvider",
    "MockRORProvider",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderPermissionError",
    "ProviderRateLimitError",
]
