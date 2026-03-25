from __future__ import annotations

import os
from typing import Any

from fastapi import Request  # noqa: TC002

from src.v2.agents import ProviderSet
from src.v2.ingest.detection import UnsupportedGitHubURL, classify_github_url
from src.v2.ingest.providers.base import (
    GitHubProvider,
    InfoscienceProvider,
    ORCIDProvider,
    RORProvider,
)
from src.v2.ingest.providers.github_provider import RealGitHubProvider
from src.v2.ingest.providers.infoscience_provider import RealInfoscienceProvider
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.ingest.providers.mock_orcid import MockORCIDProvider
from src.v2.ingest.providers.mock_ror import MockRORProvider
from src.v2.ingest.providers.orcid_provider import RealORCIDProvider
from src.v2.ingest.providers.ror_provider import RealRORProvider

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in TRUE_ENV_VALUES


def _resolve_provider_override(app_state: Any, field_name: str) -> Any | None:
    if hasattr(app_state, field_name):
        return getattr(app_state, field_name)
    return None


def _default_provider_set(
    *,
    use_mock_providers: bool,
    include_user_repositories: bool = True,
    include_organization_repositories: bool = True,
) -> ProviderSet:
    if use_mock_providers:
        return ProviderSet(
            github=MockGitHubProvider(),
            orcid=MockORCIDProvider(),
            infoscience=MockInfoscienceProvider(),
            ror=MockRORProvider(),
        )
    return ProviderSet(
        github=RealGitHubProvider(
            include_user_repositories=include_user_repositories,
            include_organization_repositories=include_organization_repositories,
        ),
        orcid=RealORCIDProvider(),
        infoscience=RealInfoscienceProvider(),
        ror=RealRORProvider(),
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
    default_provider_set = _default_provider_set(
        use_mock_providers=use_mock_providers,
        include_user_repositories=not repository_extract_scope,
        include_organization_repositories=not repository_extract_scope,
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
    )
