from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import Request  # noqa: TC002

from src.v2.agents import ProviderSet
from src.v2.config import V2Config
from src.v2.ingest.cache import SECONDS_PER_DAY, ProviderCache
from src.v2.ingest.detection import UnsupportedGitHubURL, classify_github_url
from src.v2.ingest.providers.base import (
    GitHubProvider,
    InfoscienceProvider,
    ORCIDProvider,
    RORProvider,
)
from src.v2.ingest.providers.github_provider import RealGitHubProvider
from src.v2.ingest.providers.huggingface_rag import (
    HuggingFaceRagProvider,
)
from src.v2.ingest.providers.huggingface_rag import (
    build_default_provider as build_default_huggingface_rag_provider,
)
from src.v2.ingest.providers.infoscience_provider import RealInfoscienceProvider
from src.v2.ingest.providers.infoscience_rag import (
    InfoscienceRagProvider,
)
from src.v2.ingest.providers.infoscience_rag import (
    build_default_provider as build_default_infoscience_rag_provider,
)
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.ingest.providers.mock_orcid import MockORCIDProvider
from src.v2.ingest.providers.mock_ror import MockRORProvider
from src.v2.ingest.providers.openalex_rag import (
    OpenAlexRagProvider,
)
from src.v2.ingest.providers.openalex_rag import (
    build_default_provider as build_default_openalex_rag_provider,
)
from src.v2.ingest.providers.orcid_provider import RealORCIDProvider
from src.v2.ingest.providers.orcid_rag import (
    OrcidRagProvider,
)
from src.v2.ingest.providers.orcid_rag import (
    build_default_provider as build_default_orcid_rag_provider,
)
from src.v2.ingest.providers.ror_provider import RealRORProvider
from src.v2.ingest.providers.ror_rag import (
    RorRagProvider,
)
from src.v2.ingest.providers.ror_rag import (
    build_default_provider as build_default_ror_rag_provider,
)
from src.v2.ingest.providers.zenodo_rag import (
    ZenodoRagProvider,
)
from src.v2.ingest.providers.zenodo_rag import (
    build_default_provider as build_default_zenodo_rag_provider,
)

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in TRUE_ENV_VALUES


def _resolve_provider_override(app_state: Any, field_name: str) -> Any | None:
    if hasattr(app_state, field_name):
        return getattr(app_state, field_name)
    return None


def _resolve_rag_provider(
    app_state: Any,
    *,
    state_attr: str,
    env_var: str,
    builder: Callable[[], Any | None],
    expected_type: type,
) -> Any | None:
    """Generic resolver: cache on app_state, gate on env var, build best-effort.

    The env var is treated as enabled when unset (default ``true``) so the
    five RAG providers all default to active. Returns ``None`` on disabled
    or build failure — the rest of the v2 pipeline keeps working.
    """
    existing = getattr(app_state, state_attr, None)
    if existing is not None:
        return existing if isinstance(existing, expected_type) else None

    enabled_raw = os.getenv(env_var)
    enabled = True if enabled_raw is None else _is_truthy_env(enabled_raw)
    if not enabled:
        setattr(app_state, state_attr, None)
        return None

    provider = builder()
    setattr(app_state, state_attr, provider)
    return provider


def _resolve_infoscience_rag_provider(app_state: Any) -> InfoscienceRagProvider | None:
    return _resolve_rag_provider(
        app_state,
        state_attr="v2_infoscience_rag_provider",
        env_var="V2_INFOSCIENCE_RAG_ENABLED",
        builder=build_default_infoscience_rag_provider,
        expected_type=InfoscienceRagProvider,
    )


def _resolve_huggingface_rag_provider(app_state: Any) -> HuggingFaceRagProvider | None:
    return _resolve_rag_provider(
        app_state,
        state_attr="v2_huggingface_rag_provider",
        env_var="V2_HUGGINGFACE_RAG_ENABLED",
        builder=build_default_huggingface_rag_provider,
        expected_type=HuggingFaceRagProvider,
    )


def _resolve_openalex_rag_provider(app_state: Any) -> OpenAlexRagProvider | None:
    return _resolve_rag_provider(
        app_state,
        state_attr="v2_openalex_rag_provider",
        env_var="V2_OPENALEX_RAG_ENABLED",
        builder=build_default_openalex_rag_provider,
        expected_type=OpenAlexRagProvider,
    )


def _resolve_zenodo_rag_provider(app_state: Any) -> ZenodoRagProvider | None:
    return _resolve_rag_provider(
        app_state,
        state_attr="v2_zenodo_rag_provider",
        env_var="V2_ZENODO_RAG_ENABLED",
        builder=build_default_zenodo_rag_provider,
        expected_type=ZenodoRagProvider,
    )


def _resolve_orcid_rag_provider(app_state: Any) -> OrcidRagProvider | None:
    return _resolve_rag_provider(
        app_state,
        state_attr="v2_orcid_rag_provider",
        env_var="V2_ORCID_RAG_ENABLED",
        builder=build_default_orcid_rag_provider,
        expected_type=OrcidRagProvider,
    )


def _resolve_ror_rag_provider(app_state: Any) -> RorRagProvider | None:
    return _resolve_rag_provider(
        app_state,
        state_attr="v2_ror_rag_provider",
        env_var="V2_ROR_RAG_ENABLED",
        builder=build_default_ror_rag_provider,
        expected_type=RorRagProvider,
    )


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


def _default_provider_set(  # noqa: PLR0913 — bundle-builder for ProviderSet
    *,
    use_mock_providers: bool,
    include_user_repositories: bool = True,
    include_organization_repositories: bool = True,
    cache: ProviderCache | None = None,
    infoscience_rag: InfoscienceRagProvider | None = None,
    huggingface_rag: HuggingFaceRagProvider | None = None,
    openalex_rag: OpenAlexRagProvider | None = None,
    zenodo_rag: ZenodoRagProvider | None = None,
    orcid_rag: OrcidRagProvider | None = None,
    ror_rag: RorRagProvider | None = None,
) -> ProviderSet:
    rag_kwargs: dict[str, Any] = {
        "infoscience_rag": infoscience_rag,
        "huggingface_rag": huggingface_rag,
        "openalex_rag": openalex_rag,
        "zenodo_rag": zenodo_rag,
        "orcid_rag": orcid_rag,
        "ror_rag": ror_rag,
    }
    if use_mock_providers:
        return ProviderSet(
            github=MockGitHubProvider(),
            orcid=MockORCIDProvider(),
            infoscience=MockInfoscienceProvider(),
            ror=MockRORProvider(),
            **rag_kwargs,
        )
    return ProviderSet(
        github=RealGitHubProvider(
            include_user_repositories=include_user_repositories,
            include_organization_repositories=include_organization_repositories,
            cache=cache,
        ),
        orcid=RealORCIDProvider(cache=cache),
        infoscience=RealInfoscienceProvider(cache=cache),
        ror=RealRORProvider(cache=cache),
        **rag_kwargs,
    )


def _is_repository_extract_request(request: Request) -> bool:
    full_path = request.path_params.get("full_path")
    if not isinstance(full_path, str) or not full_path.strip():
        return False

    try:
        classification = classify_github_url(full_path)
    except (UnsupportedGitHubURL, ValueError):
        return False

    detected_type = classification.detected_type
    return str(getattr(detected_type, "value", detected_type)) == "repository"


async def get_provider_set(request: Request) -> ProviderSet:
    """Return the v2 provider bundle with app-state overrides when present."""
    app_state = request.app.state
    provider_set_override = _resolve_provider_override(app_state, "v2_provider_set")
    if isinstance(provider_set_override, ProviderSet):
        return provider_set_override

    use_mock_providers = _is_truthy_env(os.getenv("V2_USE_MOCK_PROVIDERS", "true"))
    repository_extract_scope = _is_repository_extract_request(request)
    provider_cache = None if use_mock_providers else _resolve_provider_cache(app_state)
    default_provider_set = _default_provider_set(
        use_mock_providers=use_mock_providers,
        include_user_repositories=not repository_extract_scope,
        include_organization_repositories=not repository_extract_scope,
        cache=provider_cache,
        infoscience_rag=_resolve_infoscience_rag_provider(app_state),
        huggingface_rag=_resolve_huggingface_rag_provider(app_state),
        openalex_rag=_resolve_openalex_rag_provider(app_state),
        zenodo_rag=_resolve_zenodo_rag_provider(app_state),
        orcid_rag=_resolve_orcid_rag_provider(app_state),
        ror_rag=_resolve_ror_rag_provider(app_state),
    )

    github_provider = _resolve_provider_override(app_state, "v2_github_provider")
    orcid_provider = _resolve_provider_override(app_state, "v2_orcid_provider")
    infoscience_provider = _resolve_provider_override(app_state, "v2_infoscience_provider")
    ror_provider = _resolve_provider_override(app_state, "v2_ror_provider")

    return ProviderSet(
        github=github_provider if isinstance(github_provider, GitHubProvider) else default_provider_set.github,
        orcid=orcid_provider if isinstance(orcid_provider, ORCIDProvider) else default_provider_set.orcid,
        infoscience=(
            infoscience_provider
            if isinstance(infoscience_provider, InfoscienceProvider)
            else default_provider_set.infoscience
        ),
        ror=ror_provider if isinstance(ror_provider, RORProvider) else default_provider_set.ror,
        infoscience_rag=default_provider_set.infoscience_rag,
        huggingface_rag=default_provider_set.huggingface_rag,
        openalex_rag=default_provider_set.openalex_rag,
        zenodo_rag=default_provider_set.zenodo_rag,
        orcid_rag=default_provider_set.orcid_rag,
        ror_rag=default_provider_set.ror_rag,
    )
