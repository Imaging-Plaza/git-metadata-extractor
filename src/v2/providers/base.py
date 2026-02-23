from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict


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


class InfoscienceProvider(ABC):
    """Adapter interface for Infoscience metadata retrieval."""

    @abstractmethod
    def search_person(self, query: str) -> list[dict[str, Any]]:
        """Search Infoscience person profiles by query string."""

    @abstractmethod
    def search_orgunit(self, query: str) -> list[dict[str, Any]]:
        """Search Infoscience organization units by query string."""

    @abstractmethod
    def search_publications(self, query: str) -> list[dict[str, Any]]:
        """Search Infoscience publications by query string."""


class RORProvider(ABC):
    """Adapter interface for Research Organization Registry (ROR) lookups."""

    @abstractmethod
    def get_organization(self, ror_id: str) -> dict[str, Any]:
        """Fetch a ROR organization by identifier."""

    @abstractmethod
    def search_organizations(self, query: str) -> list[dict[str, Any]]:
        """Search ROR organizations by free-text query."""


class ORCIDAffiliation(TypedDict):
    """Employment or education affiliation details extracted from ORCID."""

    organization: str
    department: str | None
    role: str | None
    start_date: str | None
    end_date: str | None


class ORCIDRecord(TypedDict):
    """Normalized ORCID person payload returned by ORCID providers."""

    orcid_id: str
    name: str
    employment: list[ORCIDAffiliation]
    education: list[ORCIDAffiliation]
    affiliations: list[str]


class ORCIDProvider(ABC):
    """Adapter interface for ORCID person record lookups."""

    @abstractmethod
    def get_person_by_orcid(self, orcid_id: str) -> ORCIDRecord:
        """Return normalized ORCID profile data for a canonical ORCID identifier."""
