from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    NotRequired,
    Required,
    TypedDict,
    TypeVar,
)

if TYPE_CHECKING:
    from src.v2.providers.rate_limiter import RateLimiter

ResponseT = TypeVar("ResponseT")
INFOSCIENCE_PUBLICATION_REQUIRED_FIELDS: tuple[str, ...] = (
    "infosciencePublicationIdentifier",
    "title",
    "authors",
    "publicationDate",
    "doi",
    "url",
)
INFOSCIENCE_PUBLICATION_OPTIONAL_FIELDS: tuple[str, ...] = ("sourceOrganization",)


def _run_awaitable(value: Coroutine[Any, Any, ResponseT]) -> ResponseT:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, value)
        return future.result()


class ProviderError(RuntimeError):
    """Base exception for provider integration failures."""


class ProviderNotFoundError(ProviderError):
    """Raised when a requested resource is not found by the provider."""


class ProviderRateLimitError(ProviderError):
    """Raised when a provider rejects requests due to rate limiting."""


class ProviderPermissionError(ProviderError):
    """Raised when a provider denies access to a resource."""


class BaseProvider:
    """Base abstraction for all v2 provider adapters."""

    provider_name = "provider"

    def __init__(
        self,
        *,
        provider_name: str | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        from src.v2.providers.rate_limiter import RateLimiter  # noqa: PLC0415

        if provider_name is None:
            provider_name = self.provider_name
        self._provider_name = provider_name
        self._rate_limiter = rate_limiter or RateLimiter()

    def _run_with_rate_limit(
        self,
        request_func: Callable[[], ResponseT | Coroutine[Any, Any, ResponseT]],
    ) -> ResponseT:
        return _run_awaitable(
            self._rate_limiter.with_rate_limit(self._provider_name, request_func),
        )


class GitHubProvider(BaseProvider, ABC):
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


class InfoscienceProvider(BaseProvider, ABC):
    """Adapter interface for Infoscience metadata retrieval."""

    @abstractmethod
    def search_person(self, query: str) -> list[dict[str, Any]]:
        """Search Infoscience person profiles by query string."""

    @abstractmethod
    def search_orgunit(self, query: str) -> list[dict[str, Any]]:
        """Search Infoscience organization units by query string."""

    @abstractmethod
    def search_publications(self, query: str) -> list[InfosciencePublicationRecord]:
        """Search Infoscience publications by query string.

        Required keys in each publication payload:
        - ``infosciencePublicationIdentifier``
        - ``title``
        - ``authors``
        - ``publicationDate``
        - ``doi``
        - ``url``

        Optional keys:
        - ``sourceOrganization``
        """


class InfosciencePublicationRecord(TypedDict, total=False):
    """Normalized Infoscience publication payload contract for article generation."""

    infosciencePublicationIdentifier: Required[str | None]
    title: Required[str | None]
    authors: Required[list[str]]
    publicationDate: Required[str | None]
    doi: Required[str | None]
    url: Required[str | None]
    sourceOrganization: NotRequired[str | None]


class RORProvider(BaseProvider, ABC):
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


class ORCIDProvider(BaseProvider, ABC):
    """Adapter interface for ORCID person record lookups."""

    @abstractmethod
    def get_person_by_orcid(self, orcid_id: str) -> ORCIDRecord:
        """Return normalized ORCID profile data for a canonical ORCID identifier."""
