"""Optional OAuth 2-legged (`client_credentials`) helper for ORCID.

ORCID's anonymous public-API endpoints are rate-limited per IP and trip a
multi-hour throttle on bursty workloads. With a free `/read-public` OAuth
client (registered at https://orcid.org/developer-tools), the same endpoints
become authenticated and use a separate, much larger quota bucket.

This helper is intentionally optional: callers should treat a `None` return
as "fall back to anonymous access" — never fail the pipeline on a missing
OAuth client.
"""

from __future__ import annotations

import logging

import requests

LOGGER = logging.getLogger(__name__)

ORCID_TOKEN_URL = "https://orcid.org/oauth/token"
ORCID_TOKEN_SCOPE = "/read-public"


def fetch_access_token(
    *,
    client_id: str | None,
    client_secret: str | None,
    token_url: str = ORCID_TOKEN_URL,
    scope: str = ORCID_TOKEN_SCOPE,
    timeout_seconds: int = 20,
) -> str | None:
    """Exchange `client_id` / `client_secret` for a `/read-public` access token.

    Returns the token on success, `None` when credentials are missing or the
    OAuth POST fails for any reason (so callers can keep the anonymous path).
    The `/read-public` token is long-lived (~20 years per ORCID's docs) and
    can be cached for the lifetime of the process.
    """
    if not client_id or not client_secret:
        return None
    try:
        response = requests.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
                "scope": scope,
            },
            headers={"Accept": "application/json"},
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        LOGGER.warning("ORCID OAuth token request failed: %s", exc)
        return None
    if response.status_code != 200:
        LOGGER.warning(
            "ORCID OAuth token endpoint returned HTTP %s: %s",
            response.status_code,
            response.text[:200],
        )
        return None
    payload = response.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        LOGGER.warning("ORCID OAuth response missing access_token: %s", payload)
        return None
    LOGGER.info(
        "ORCID OAuth token acquired (scope=%s, expires_in=%ss)",
        payload.get("scope"),
        payload.get("expires_in"),
    )
    return token
