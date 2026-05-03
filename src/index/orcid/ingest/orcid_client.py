"""Build a `RealORCIDProvider` configured for the indexer.

We reuse the production-grade ORCID provider already maintained at
`src/v2/ingest/providers/orcid_provider.py` rather than reinventing
record fetch + expanded-search.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

from src.v2.ingest.providers.orcid_provider import RealORCIDProvider
from src.v2.ingest.providers.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig

LOGGER = logging.getLogger(__name__)


def _fetch_orcid_access_token(config: OrcidIndexConfig) -> str | None:
    """Index-side wrapper around the shared OAuth helper.

    Reads credentials + endpoint from `OrcidApiConfig` and delegates to
    `src.v2.ingest.providers.orcid_oauth.fetch_access_token`. Returns None
    when credentials are absent or the OAuth POST fails.
    """
    from src.v2.ingest.providers.orcid_oauth import fetch_access_token

    return fetch_access_token(
        client_id=config.orcid.client_id,
        client_secret=config.orcid.client_secret,
        token_url=config.orcid.oauth_token_url,
        scope=config.orcid.oauth_scope,
        timeout_seconds=config.orcid.timeout_seconds,
    )


def build_orcid_provider(config: OrcidIndexConfig) -> RealORCIDProvider:
    """Construct an ORCID provider wired to this indexer's config."""
    limiter = RateLimiter(
        max_retries=config.orcid.max_retries,
        base_delay_seconds=config.orcid.base_delay_seconds,
        max_delay_seconds=config.orcid.max_delay_seconds,
    )
    session: requests.Session | None = None
    headers: dict[str, str] = {}
    if config.orcid.user_agent:
        headers["User-Agent"] = config.orcid.user_agent
    access_token = _fetch_orcid_access_token(config)
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if headers:
        session = requests.Session()
        session.headers.update(headers)
    return RealORCIDProvider(
        base_url=config.orcid.base_url,
        timeout=config.orcid.timeout_seconds,
        rate_limiter=limiter,
        session=session,
    )
