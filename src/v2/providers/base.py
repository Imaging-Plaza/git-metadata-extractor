from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProviderError(RuntimeError):
    """Base exception for provider integration failures."""


class ProviderNotFoundError(ProviderError):
    """Raised when a requested resource is not found by the provider."""


class ProviderRateLimitError(ProviderError):
    """Raised when a provider rejects requests due to rate limiting."""


class ProviderPermissionError(ProviderError):
    """Raised when a provider denies access to a resource."""


class GitHubProvider(ABC):
    """Adapter interface for GitHub metadata retrieval."""

    @abstractmethod
    def get_repository(self, full_name: str) -> dict[str, Any]:
        """Return repository metadata for ``owner/repo``."""

    @abstractmethod
    def get_user(self, username: str) -> dict[str, Any]:
        """Return user profile metadata for ``username``."""

    @abstractmethod
    def get_organization(self, org_name: str) -> dict[str, Any]:
        """Return organization profile metadata for ``org_name``."""

    @abstractmethod
    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        """Return repository contributors for ``owner/repo``."""

    @abstractmethod
    def get_languages(self, full_name: str) -> dict[str, int]:
        """Return language byte counts for ``owner/repo``."""
