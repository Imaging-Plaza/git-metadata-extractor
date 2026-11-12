"""Transport-independent provider composition and resource ownership."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from types import SimpleNamespace
from typing import Any, Callable

from pydantic import Field, PrivateAttr

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.config import V2Config
from git_metadata_extractor.models import InternalModel
from git_metadata_extractor.providers.base import (
    GitHubProvider,
    InfoscienceProvider,
    ORCIDProvider,
    RORProvider,
)
from git_metadata_extractor.providers.cache import SECONDS_PER_DAY, ProviderCache
from git_metadata_extractor.providers.epfl_graph_rag import (
    EpflGraphRagProvider,
)
from git_metadata_extractor.providers.epfl_graph_rag import (
    build_default_provider as build_default_epfl_graph_rag_provider,
)
from git_metadata_extractor.providers.ethz_research_collection_rag import (
    EthzResearchCollectionRagProvider,
)
from git_metadata_extractor.providers.ethz_research_collection_rag import (
    build_default_provider as build_default_ethz_research_collection_rag_provider,
)
from git_metadata_extractor.providers.federated_rag import (
    FederatedRagProvider,
)
from git_metadata_extractor.providers.federated_rag import (
    build_default_provider as build_default_federated_rag_provider,
)
from git_metadata_extractor.providers.github_provider import RealGitHubProvider
from git_metadata_extractor.providers.github_rag import (
    GitHubRagProvider,
)
from git_metadata_extractor.providers.github_rag import (
    build_default_provider as build_default_github_rag_provider,
)
from git_metadata_extractor.providers.huggingface_rag import (
    HuggingFaceRagProvider,
)
from git_metadata_extractor.providers.huggingface_rag import (
    build_default_provider as build_default_huggingface_rag_provider,
)
from git_metadata_extractor.providers.infoscience_provider import (
    RealInfoscienceProvider,
)
from git_metadata_extractor.providers.infoscience_rag import (
    InfoscienceRagProvider,
)
from git_metadata_extractor.providers.infoscience_rag import (
    build_default_provider as build_default_infoscience_rag_provider,
)
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.providers.mock_orcid import MockORCIDProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider
from git_metadata_extractor.providers.oamonitor_rag import (
    OamonitorRagProvider,
)
from git_metadata_extractor.providers.oamonitor_rag import (
    build_default_provider as build_default_oamonitor_rag_provider,
)
from git_metadata_extractor.providers.openalex_rag import (
    OpenAlexRagProvider,
)
from git_metadata_extractor.providers.openalex_rag import (
    build_default_provider as build_default_openalex_rag_provider,
)
from git_metadata_extractor.providers.orcid_oauth import (
    fetch_access_token as _fetch_orcid_token,
)
from git_metadata_extractor.providers.orcid_provider import RealORCIDProvider
from git_metadata_extractor.providers.orcid_rag import (
    OrcidRagProvider,
)
from git_metadata_extractor.providers.orcid_rag import (
    build_default_provider as build_default_orcid_rag_provider,
)
from git_metadata_extractor.providers.package_registry_provider import (
    PackageRegistryProvider,
)
from git_metadata_extractor.providers.renkulab_rag import (
    RenkulabRagProvider,
)
from git_metadata_extractor.providers.renkulab_rag import (
    build_default_provider as build_default_renkulab_rag_provider,
)
from git_metadata_extractor.providers.ror_provider import RealRORProvider
from git_metadata_extractor.providers.ror_rag import (
    RorRagProvider,
)
from git_metadata_extractor.providers.ror_rag import (
    build_default_provider as build_default_ror_rag_provider,
)
from git_metadata_extractor.providers.snsf_rag import (
    SnsfRagProvider,
)
from git_metadata_extractor.providers.snsf_rag import (
    build_default_provider as build_default_snsf_rag_provider,
)
from git_metadata_extractor.providers.sources_service import SourcesClient
from git_metadata_extractor.providers.swissubase_rag import (
    SwissubaseRagProvider,
)
from git_metadata_extractor.providers.swissubase_rag import (
    build_default_provider as build_default_swissubase_rag_provider,
)
from git_metadata_extractor.providers.zenodo_rag import (
    ZenodoRagProvider,
)
from git_metadata_extractor.providers.zenodo_rag import (
    build_default_provider as build_default_zenodo_rag_provider,
)

logger = logging.getLogger(__name__)
_UNSET_CACHE = object()
TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in TRUE_ENV_VALUES


def _resolve_provider_cache(app_state: Any) -> ProviderCache | None:
    existing = getattr(app_state, "v2_provider_cache", None)
    if isinstance(existing, ProviderCache):
        return existing
    if not _is_truthy_env(os.getenv("V2_PROVIDER_CACHE_ENABLED", "true")):
        return None
    config = V2Config()
    cache = ProviderCache(
        config.V2_PROVIDER_CACHE_PATH,
        default_ttl_seconds=config.V2_PROVIDER_CACHE_TTL_DAYS * SECONDS_PER_DAY,
    )
    app_state.v2_provider_cache = cache
    return cache


def _resolve_package_registry_provider(
    cache: ProviderCache | None,
) -> PackageRegistryProvider | None:
    """Build the npm/PyPI registry provider for the real ProviderSet path.

    Gated by ``V2_PACKAGE_REGISTRY_ENABLED`` (default ``true``); the usual
    falsey spellings (``false`` / ``0`` / ``no`` / ``off``) disable it and
    return None so registry discovery is skipped entirely.
    """
    if not _is_truthy_env(os.getenv("V2_PACKAGE_REGISTRY_ENABLED", "true")):
        return None
    return PackageRegistryProvider(cache=cache)


def _optional_orcid_oauth_session() -> Any:
    """Build an authenticated `requests.Session` if ORCID OAuth credentials
    are present in the environment; return `None` otherwise so the provider
    falls back to anonymous public-API access.

    Reads `ORCID_CLIENT_ID` and `ORCID_CLIENT_SECRET` (free `/read-public`
    client registered at https://orcid.org/developer-tools). Both must be
    set; either one missing → anonymous fallback.
    """
    import requests  # noqa: PLC0415 — local import keeps base import budget tight.

    client_id = os.getenv("ORCID_CLIENT_ID") or None
    client_secret = os.getenv("ORCID_CLIENT_SECRET") or None
    if not client_id or not client_secret:
        return None
    token = _fetch_orcid_token(client_id=client_id, client_secret=client_secret)
    if not token:
        return None
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    session.headers["User-Agent"] = "git-metadata-extractor/0.1"
    return session


def unique_resources(resources):
    seen = set()
    for resource in resources:
        if resource is not None and id(resource) not in seen:
            seen.add(id(resource))
            yield resource


async def close_resources(resources) -> None:
    for resource in unique_resources(resources):
        close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
        if callable(close):
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001 - cleanup continues for remaining resources
                logger.warning("Provider cleanup failed (%s)", type(exc).__name__)


def close_resources_sync(resources) -> None:
    """Compatibility for synchronous callers; async owners should await aclose."""
    for resource in unique_resources(resources):
        close = getattr(resource, "close", None)
        if callable(close) and not inspect.iscoroutinefunction(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Provider cleanup failed (%s)", type(exc).__name__)


class ProviderResources(InternalModel):
    bundle: ProviderSet | None = None
    owned: list[Any] = Field(default_factory=list)
    _close_task: asyncio.Task[None] | None = PrivateAttr(default=None)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(close_resources(self.owned))
        # A second cancellation must not interrupt cleanup halfway through.
        await asyncio.shield(self._close_task)


class ProviderRegistration(InternalModel, frozen=True):
    name: str
    env_var: str
    builder: Callable
    expected_type: type


RAG_PROVIDERS = (
    ProviderRegistration(
        name="infoscience_rag",
        env_var="V2_INFOSCIENCE_RAG_ENABLED",
        builder=build_default_infoscience_rag_provider,
        expected_type=InfoscienceRagProvider,
    ),
    ProviderRegistration(
        name="ethz_research_collection_rag",
        env_var="V2_ETHZ_RESEARCH_COLLECTION_RAG_ENABLED",
        builder=build_default_ethz_research_collection_rag_provider,
        expected_type=EthzResearchCollectionRagProvider,
    ),
    ProviderRegistration(
        name="huggingface_rag",
        env_var="V2_HUGGINGFACE_RAG_ENABLED",
        builder=build_default_huggingface_rag_provider,
        expected_type=HuggingFaceRagProvider,
    ),
    ProviderRegistration(
        name="openalex_rag",
        env_var="V2_OPENALEX_RAG_ENABLED",
        builder=build_default_openalex_rag_provider,
        expected_type=OpenAlexRagProvider,
    ),
    ProviderRegistration(
        name="epfl_graph_rag",
        env_var="V2_EPFL_GRAPH_RAG_ENABLED",
        builder=build_default_epfl_graph_rag_provider,
        expected_type=EpflGraphRagProvider,
    ),
    ProviderRegistration(
        name="zenodo_rag",
        env_var="V2_ZENODO_RAG_ENABLED",
        builder=build_default_zenodo_rag_provider,
        expected_type=ZenodoRagProvider,
    ),
    ProviderRegistration(
        name="oamonitor_rag",
        env_var="V2_OAMONITOR_RAG_ENABLED",
        builder=build_default_oamonitor_rag_provider,
        expected_type=OamonitorRagProvider,
    ),
    ProviderRegistration(
        name="github_rag",
        env_var="V2_GITHUB_RAG_ENABLED",
        builder=build_default_github_rag_provider,
        expected_type=GitHubRagProvider,
    ),
    ProviderRegistration(
        name="renkulab_rag",
        env_var="V2_RENKULAB_RAG_ENABLED",
        builder=build_default_renkulab_rag_provider,
        expected_type=RenkulabRagProvider,
    ),
    ProviderRegistration(
        name="orcid_rag",
        env_var="V2_ORCID_RAG_ENABLED",
        builder=build_default_orcid_rag_provider,
        expected_type=OrcidRagProvider,
    ),
    ProviderRegistration(
        name="ror_rag", env_var="V2_ROR_RAG_ENABLED", builder=build_default_ror_rag_provider, expected_type=RorRagProvider,
    ),
    ProviderRegistration(
        name="snsf_rag",
        env_var="V2_SNSF_RAG_ENABLED",
        builder=build_default_snsf_rag_provider,
        expected_type=SnsfRagProvider,
    ),
    ProviderRegistration(
        name="swissubase_rag",
        env_var="V2_SWISSUBASE_RAG_ENABLED",
        builder=build_default_swissubase_rag_provider,
        expected_type=SwissubaseRagProvider,
    ),
    ProviderRegistration(
        name="federated_rag",
        env_var="V2_FEDERATED_RAG_ENABLED",
        builder=build_default_federated_rag_provider,
        expected_type=FederatedRagProvider,
    ),
)


def _shared_resources(state) -> ProviderResources:
    resources = getattr(state, "v2_shared_provider_resources", None)
    if resources is None:
        resources = ProviderResources()
        state.v2_shared_provider_resources = resources
    return resources


async def close_shared_providers(state) -> None:
    resources = getattr(state, "v2_shared_provider_resources", None)
    if resources is not None:
        await resources.aclose()


def _resolve_registered_provider(state, registration):
    attribute = f"v2_{registration.name}_provider"
    existing = getattr(state, attribute, None)
    if existing is not None:
        return existing if isinstance(existing, registration.expected_type) else None
    provider = None
    if _is_truthy_env(os.getenv(registration.env_var, "true")):
        try:
            provider = registration.builder()
        except Exception as exc:  # noqa: BLE001 - optional index unavailability is nonfatal
            logger.warning(
                "Provider %s unavailable (%s)", registration.name, type(exc).__name__,
            )
        if provider is not None:
            _shared_resources(state).owned.append(provider)
    setattr(state, attribute, provider)
    return provider


def _build_index_providers(state) -> dict:
    """Compose HTTP adapters; an unconfigured service has no index backend."""
    remote = getattr(state, "v2_sources_provider", None)
    url = os.getenv("SOURCES_API_URL", "").strip()
    if not url and not isinstance(remote, SourcesClient):
        return {}
    if not isinstance(remote, SourcesClient):
        remote = SourcesClient(
            url, os.getenv("SOURCES_API_TOKEN", ""),
            timeout=float(os.getenv("SOURCES_API_TIMEOUT_SECONDS", "30")),
        )
        state.v2_sources_provider = remote
        _shared_resources(state).owned.append(remote)
    values = {"sources": remote}
    for spec in RAG_PROVIDERS:
        attribute = f"v2_{spec.name}_provider"
        existing = getattr(state, attribute, None)
        if isinstance(existing, spec.expected_type):
            values[spec.name] = existing
            continue
        if not _is_truthy_env(os.getenv(spec.env_var, "true")):
            continue
        values[spec.name] = spec.expected_type(client=remote)
        setattr(state, attribute, values[spec.name])
    return values


def build_provider_resources(  # noqa: PLR0913 - provider composition controls
    state,
    *,
    use_mock: bool | None = None,
    repository_scope: bool = False,
    include_rag: bool = True,
    cache: Any = _UNSET_CACHE,
    include_user_repositories: bool | None = None,
    include_organization_repositories: bool | None = None,
) -> ProviderResources:
    """Injected providers are borrowed; primary clients are owned per call.

    RAG clients built here belong to state and are closed by its owner at shutdown.
    No HTTP objects or event-loop mutation are required to compose the bundle.
    """
    injected = getattr(state, "v2_provider_set", None)
    if isinstance(injected, ProviderSet):
        return ProviderResources(bundle=injected)
    if use_mock is None:
        use_mock = _is_truthy_env(os.getenv("V2_USE_MOCK_PROVIDERS", "true"))
    if cache is _UNSET_CACHE:
        cache = None if use_mock else _resolve_provider_cache(state)
    factories = {
        "github": (
            GitHubProvider,
            lambda: (
                MockGitHubProvider()
                if use_mock
                else RealGitHubProvider(
                    include_user_repositories=not repository_scope
                    if include_user_repositories is None
                    else include_user_repositories,
                    include_organization_repositories=not repository_scope
                    if include_organization_repositories is None
                    else include_organization_repositories,
                    cache=cache,
                )
            ),
        ),
        "orcid": (
            ORCIDProvider,
            lambda: (
                MockORCIDProvider()
                if use_mock
                else RealORCIDProvider(
                    cache=cache, session=_optional_orcid_oauth_session(),
                )
            ),
        ),
        "infoscience": (
            InfoscienceProvider,
            lambda: (
                MockInfoscienceProvider()
                if use_mock
                else RealInfoscienceProvider(cache=cache)
            ),
        ),
        "ror": (
            RORProvider,
            lambda: MockRORProvider() if use_mock else RealRORProvider(cache=cache),
        ),
        "package_registry": (
            PackageRegistryProvider,
            lambda: None if use_mock else _resolve_package_registry_provider(cache),
        ),
    }
    owned, values = [], {}
    try:
        for name, (expected_type, factory) in factories.items():
            override = getattr(state, f"v2_{name}_provider", None)
            if isinstance(override, expected_type):
                values[name] = override
            else:
                values[name] = factory()
                if values[name] is not None:
                    owned.append(values[name])
        if include_rag:
            values.update(_build_index_providers(state))
    except BaseException:
        # Primary factories are synchronous and own synchronous HTTP clients.
        close_resources_sync(owned)
        raise
    return ProviderResources(bundle=ProviderSet(**values), owned=owned)


def _default_provider_set(
    *,
    use_mock_providers,
    include_user_repositories=True,
    include_organization_repositories=True,
    cache=None,
    **rag_providers,
):
    """Compatibility factory for development scripts; caller owns this bundle."""
    state = SimpleNamespace(v2_provider_cache=cache)
    resources = build_provider_resources(
        state,
        use_mock=use_mock_providers,
        include_rag=False,
        cache=cache,
        include_user_repositories=include_user_repositories,
        include_organization_repositories=include_organization_repositories,
    )
    bundle = resources.bundle
    return bundle.model_copy(update=rag_providers)
