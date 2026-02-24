"""Provider interfaces and mocks for the v2 extraction pipeline."""

from src.v2.providers.base import (
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
from src.v2.providers.github_provider import RealGitHubProvider
from src.v2.providers.infoscience_provider import RealInfoscienceProvider
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_orcid import MockORCIDProvider
from src.v2.providers.mock_ror import MockRORProvider
from src.v2.providers.orcid_provider import RealORCIDProvider
from src.v2.providers.rate_limiter import RateLimiter
from src.v2.providers.ror_provider import RealRORProvider

_ALIASES = {
    "github": "github",
    "info": "infoscience",
    "infoscience": "infoscience",
    "orcid": "orcid",
    "ror": "ror",
}

_REAL_PROVIDERS = {
    "github": RealGitHubProvider,
    "infoscience": RealInfoscienceProvider,
    "orcid": RealORCIDProvider,
    "ror": RealRORProvider,
}

_MOCK_PROVIDERS = {
    "github": MockGitHubProvider,
    "infoscience": MockInfoscienceProvider,
    "orcid": MockORCIDProvider,
    "ror": MockRORProvider,
}


def get_provider(name: str, *, use_mock: bool = False, **kwargs: object) -> BaseProvider:
    """Return a configured provider implementation by name."""
    normalized_name = _ALIASES.get(name.strip().lower())
    providers = _MOCK_PROVIDERS if use_mock else _REAL_PROVIDERS
    provider_cls = providers.get(normalized_name or "")

    if provider_cls is None:
        message = f"Unknown provider name: {name}"
        raise ValueError(message)

    return provider_cls(**kwargs)

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
    "get_provider",
]
