from __future__ import annotations

import asyncio
import contextvars
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
    from src.v2.ingest.providers.rate_limiter import RateLimiter

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
    """Run a coroutine to completion, preserving the calling ContextVar state.

    See `src/v2/ingest/providers/infoscience_provider.py:_run_async` for why
    we copy the context (request-id propagation into worker threads).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(ctx.run, asyncio.run, value)
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
        from src.v2.ingest.providers.rate_limiter import RateLimiter  # noqa: PLC0415

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

    def get_repository_sbom(self, full_name: str) -> list[dict[str, Any]] | None:
        """Return the parsed SPDX dependency list for ``owner/repo``.

        Each entry is a dict with keys ``name``, ``ecosystem``, ``version``,
        and ``spdxId``. Returns ``None`` when the repository has no SBOM
        available (e.g. dependency graph disabled, private repo without the
        required scope, or 404).

        Default implementation returns ``None`` so providers that do not
        expose dependency data (test fakes, partial mocks) need not stub
        this. Implementations must not raise on missing SBOMs — only on
        transport-level failures.
        """
        del full_name
        return None

    def get_repository_jsonld(self, full_name: str) -> dict[str, Any]:
        """Return the raw GIMIE JSON-LD payload for ``owner/repo``, or an empty dict."""
        return {}

    def get_repository_readme(self, full_name: str) -> str:
        """Return the repository README content for ``owner/repo``, or empty string."""
        del full_name
        return ""

    def get_profile_readme(self, owner: str, *, is_organization: bool) -> str:
        """Return the GitHub *profile* README — the markdown shown on a user's
        or organization's profile page — or an empty string when none exists.

        Profile READMEs live in a special location: ``<user>/<user>``'s
        README for a user, ``<org>/.github``'s ``profile/README.md`` for an
        organization.
        """
        del owner, is_organization
        return ""

    def get_repository_aux_files(self, full_name: str) -> dict[str, str]:
        """Return repo-root attribution files (AUTHORS, NOTICE, …) keyed by filename."""
        del full_name
        return {}

    def get_repository_root_entries(self, full_name: str) -> list[str] | None:
        """Return the names of all entries at the repository root, or None on failure.

        Used to detect CI configuration files (.github, .travis.yml, etc.)
        without fetching their contents. Default returns None so providers
        that don't expose directory listings (test fakes, partial mocks) need
        not stub this. Must not raise on absence — only transport-level
        failures are exceptional; return None instead.
        """
        del full_name
        return None

    def get_repository_releases(self, full_name: str) -> list[dict[str, Any]]:
        """Return published releases for ``owner/repo``, newest first.

        Each entry is a thinned release dict (``tag_name``, ``name``,
        dates, ``draft`` / ``prerelease`` flags, ``html_url``, and
        ``assets``). Default implementation returns an empty list so
        providers that don't surface releases (test fakes, partial
        mocks) need not stub it. Must not raise on absence — only
        transport-level failures are exceptional.
        """
        del full_name
        return []

    def get_repository_container_images(self, full_name: str) -> list[dict[str, Any]]:
        """Return GHCR container (Docker) images published from ``owner/repo``.

        GitHub packages are owner-scoped — there is no per-repo packages
        endpoint — so implementations list the owner's ``container``
        packages and keep those linked to (or named after) the repo,
        each with its image reference and tags. Requires the
        ``read:packages`` token scope; without it implementations
        degrade to an empty list rather than raising. Default returns
        ``[]``.
        """
        del full_name
        return []


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
    author_authorities: NotRequired[list[str | None]]
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


class ORCIDSearchHit(TypedDict):
    """Single hit returned by the ORCID expanded-search endpoint."""

    orcid_id: str | None
    given_names: str | None
    family_names: str | None
    credit_name: str | None
    other_names: list[str]
    institution_names: list[str]
    emails: list[str]


class ORCIDProvider(BaseProvider, ABC):
    """Adapter interface for ORCID person record lookups."""

    @abstractmethod
    def get_person_by_orcid(self, orcid_id: str) -> ORCIDRecord:
        """Return normalized ORCID profile data for a canonical ORCID identifier."""

    @abstractmethod
    def search_persons(
        self,
        query: str,
        *,
        rows: int = 50,
        start: int = 0,
    ) -> list[ORCIDSearchHit]:
        """Search ORCID expanded-search for persons matching ``query``."""
