"""GitHub token-pool rate-limit probe.

Probes each token in `GME_GITHUB_TOKEN_POOL` (or `GME_GITHUB_TOKEN`) against
`GET /rate_limit`. The endpoint does not count against the rate limit
itself, so this is safe to call from `/v2/health`. Results are cached
in-process for 30s to avoid flooding GitHub when a load balancer hits
`/health` repeatedly.

The summary surfaced in the response carries no token values — only
position-indexed status — so callers (batch scripts, dashboards) can
back off when all tokens are exhausted without needing the secret.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_RATE_LIMIT_URL = "https://api.github.com/rate_limit"
_PROBE_TIMEOUT_SECONDS = 5.0
_CACHE_TTL_SECONDS = 30.0
_LOW_REMAINING_THRESHOLD = 200


class GitHubTokenStatus(BaseModel):
    """Per-token state. `index` is the 1-based pool position; the token
    value itself is never exposed."""

    index: int
    status: Literal["ok", "rate_limited", "invalid", "unreachable"]
    core_remaining: int | None = None
    core_limit: int | None = None
    core_reset: datetime | None = None


class GitHubRateLimitSummary(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    total_remaining: int
    earliest_reset: datetime | None
    tokens: list[GitHubTokenStatus]


_cache_lock = threading.Lock()
_cache: tuple[float, GitHubRateLimitSummary] | None = None


def _resolve_token_pool() -> list[str]:
    pool = os.environ.get("GME_GITHUB_TOKEN_POOL", "")
    tokens = [token.strip() for token in pool.split(",") if token.strip()]
    if tokens:
        return tokens
    single = os.environ.get("GME_GITHUB_TOKEN", "").strip()
    return [single] if single else []


def _probe_one(index: int, token: str) -> GitHubTokenStatus:
    try:
        response = httpx.get(
            _RATE_LIMIT_URL,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "GitMetadataExtractor/2.0",
            },
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("github rate-limit probe (token #%d) failed: %s", index, exc)
        return GitHubTokenStatus(index=index, status="unreachable")

    if response.status_code == 401:
        return GitHubTokenStatus(index=index, status="invalid")
    if response.status_code != 200:
        logger.warning(
            "github rate-limit probe (token #%d) returned http=%d",
            index,
            response.status_code,
        )
        return GitHubTokenStatus(index=index, status="unreachable")

    payload = response.json()
    core = payload.get("resources", {}).get("core") or payload.get("rate") or {}
    remaining = int(core.get("remaining", 0))
    limit = int(core.get("limit", 0))
    reset_ts = core.get("reset")
    reset_dt = (
        datetime.fromtimestamp(int(reset_ts), tz=timezone.utc)
        if isinstance(reset_ts, (int, float))
        else None
    )
    state: Literal["ok", "rate_limited"] = (
        "ok" if remaining > 0 else "rate_limited"
    )
    return GitHubTokenStatus(
        index=index,
        status=state,
        core_remaining=remaining,
        core_limit=limit,
        core_reset=reset_dt,
    )


def _summarize(token_statuses: list[GitHubTokenStatus]) -> GitHubRateLimitSummary:
    if not token_statuses:
        return GitHubRateLimitSummary(
            status="unhealthy",
            total_remaining=0,
            earliest_reset=None,
            tokens=[],
        )

    if all(t.status == "invalid" for t in token_statuses):
        return GitHubRateLimitSummary(
            status="unhealthy",
            total_remaining=0,
            earliest_reset=None,
            tokens=token_statuses,
        )

    total_remaining = sum(
        t.core_remaining or 0
        for t in token_statuses
        if t.status in {"ok", "rate_limited"}
    )
    resets = [t.core_reset for t in token_statuses if t.core_reset is not None]
    earliest_reset = min(resets) if resets else None

    has_capacity = any(t.status == "ok" for t in token_statuses)
    if not has_capacity:
        # All probed tokens are rate-limited or unreachable.
        status: Literal["healthy", "degraded", "unhealthy"] = "degraded"
    elif total_remaining < _LOW_REMAINING_THRESHOLD:
        status = "degraded"
    else:
        status = "healthy"

    return GitHubRateLimitSummary(
        status=status,
        total_remaining=total_remaining,
        earliest_reset=earliest_reset,
        tokens=token_statuses,
    )


def probe_github_rate_limit() -> GitHubRateLimitSummary:
    """Return the current rate-limit summary, cached for 30s."""

    global _cache  # noqa: PLW0603
    now = time.monotonic()
    with _cache_lock:
        if _cache is not None:
            cached_at, cached_summary = _cache
            if now - cached_at < _CACHE_TTL_SECONDS:
                return cached_summary

    tokens = _resolve_token_pool()
    statuses = [_probe_one(i + 1, token) for i, token in enumerate(tokens)]
    summary = _summarize(statuses)

    with _cache_lock:
        _cache = (now, summary)
    return summary


def reset_cache() -> None:
    """Clear the in-process cache. Test hook."""

    global _cache  # noqa: PLW0603
    with _cache_lock:
        _cache = None
