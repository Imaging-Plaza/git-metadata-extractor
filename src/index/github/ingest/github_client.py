"""Direct GitHub REST client for the index module.

Why not reuse `src.v2.ingest.providers.github_provider.RealGitHubProvider`?

That provider runs gimie under the hood (clones the repo, extracts JSON-LD,
SBOM) — which is overkill for an index that only needs metadata + README.
We use the same auth + ProviderCache pattern that
`RealGitHubProvider._get_repository_rest_metadata` uses, just trimmed to
the four endpoints we actually need:

  GET /repos/{owner}/{name}             — full repo payload
  GET /repos/{owner}/{name}/languages   — {lang: bytes}
  GET /repos/{owner}/{name}/contributors?per_page=100 — top-100 contributors
  GET /repos/{owner}/{name}/readme      — base64-encoded README + path

Multi-token support: `GITHUB_TOKEN` may be a comma-separated list of PATs
(`ghp_A,ghp_B,...`). The client splits at construction, round-robins across
tokens per request, and on 403 rate-limit responses parks the exhausted
token until its `X-RateLimit-Reset` timestamp. With N tokens, effective
throughput is N × 5K req/h (subject to the secondary abuse-detection
limits which are per-account).

The cache TTL matches the v2 default (30 days; configurable via
`V2_PROVIDER_CACHE_TTL_DAYS`). Cache hits are silent log lines so a cold
re-run re-uses the data without a single REST call.
"""

from __future__ import annotations

import base64
import itertools
import logging
import time
from pathlib import Path
from typing import Any

import requests

from src.v2.ingest.cache import ProviderCache

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30
RATE_LIMIT_SLEEP_PADDING_SECONDS = 5


class GitHubClient:
    """Thin REST client with token-rotation and shared `ProviderCache`."""

    def __init__(
        self,
        *,
        api_base: str,
        token: str | None,
        cache_path: Path,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        # Comma-separated list → list of tokens; empty → anonymous.
        raw = (token or "").strip()
        self._tokens: list[str] = [t.strip() for t in raw.split(",") if t.strip()]
        # Per-token cooldown: token → epoch seconds when it becomes usable again.
        self._cooldowns: dict[str, float] = {}
        self._token_cycle = itertools.cycle(self._tokens) if self._tokens else None
        self._cache = ProviderCache(cache_path)
        if self._tokens:
            LOGGER.info("github client: %d token(s) loaded", len(self._tokens))
        else:
            LOGGER.warning("github client: no token configured (anonymous, 60 req/h)")

    def _next_available_token(self) -> str | None:
        """Return the next token whose cooldown has elapsed, or sleep until one is.

        Returns None when no tokens are configured (anonymous mode).
        """
        if not self._tokens or self._token_cycle is None:
            return None
        # Rotate up to len(tokens) times to find a hot one.
        for _ in range(len(self._tokens)):
            candidate = next(self._token_cycle)
            cooldown_until = self._cooldowns.get(candidate, 0.0)
            if cooldown_until <= time.time():
                return candidate
        # All tokens parked — sleep until the soonest reset.
        soonest = min(self._cooldowns.values())
        delay = max(soonest - time.time(), 0) + RATE_LIMIT_SLEEP_PADDING_SECONDS
        LOGGER.warning(
            "github client: all %d token(s) rate-limited; sleeping %.0fs",
            len(self._tokens),
            delay,
        )
        time.sleep(delay)
        # After the sleep, the soonest-resetting token is now hot.
        return next(self._token_cycle)

    def _headers(self) -> tuple[dict[str, str], str | None]:
        token = self._next_available_token()
        h = {"Accept": "application/vnd.github+json"}
        if token:
            h["Authorization"] = f"token {token}"
        return h, token

    def _park_token(self, token: str, response: requests.Response) -> None:
        """Mark `token` as cooled-down until `X-RateLimit-Reset`."""
        reset_header = response.headers.get("X-RateLimit-Reset")
        try:
            reset_ts = float(reset_header) if reset_header else time.time() + 60
        except ValueError:
            reset_ts = time.time() + 60
        self._cooldowns[token] = reset_ts
        LOGGER.warning(
            "github client: token %s… rate-limited; parking %.0fs",
            token[:6],
            max(reset_ts - time.time(), 0),
        )

    def _get_json(self, url: str) -> Any:
        # Up to len(tokens)+1 attempts: each rotation tries a different token.
        attempts = max(len(self._tokens) + 1, 1)
        for _ in range(attempts):
            headers, token_used = self._headers()
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException:
                LOGGER.exception("github GET failed: %s", url)
                return None
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    LOGGER.exception("github GET response not JSON: %s", url)
                    return None
            if response.status_code == 404:
                LOGGER.info("github GET 404: %s", url)
                return None
            if response.status_code in (403, 429) and token_used is not None:
                # Rate-limited or secondary-abuse-detection. Park this token,
                # rotate, and retry on a fresh one.
                remaining = response.headers.get("X-RateLimit-Remaining")
                if remaining == "0" or response.status_code == 429:
                    self._park_token(token_used, response)
                    continue
                LOGGER.warning(
                    "github GET %d (not rate-limit) for %s",
                    response.status_code,
                    url,
                )
                return None
            LOGGER.warning(
                "github GET returned %d for %s",
                response.status_code,
                url,
            )
            return None
        LOGGER.warning("github GET exhausted retries: %s", url)
        return None

    # ---- Public methods --------------------------------------------------

    def get_repository(self, full_name: str) -> dict[str, Any] | None:
        url = f"{self._api_base}/repos/{full_name}"
        key = ProviderCache.make_key("github_index", "get_repository", full_name=full_name)
        return self._cache.get_or_set(
            key,
            lambda: self._get_json(url),
            label=f"github_index.get_repository({full_name})",
        )

    def get_languages(self, full_name: str) -> dict[str, int]:
        url = f"{self._api_base}/repos/{full_name}/languages"
        key = ProviderCache.make_key("github_index", "get_languages", full_name=full_name)
        result = self._cache.get_or_set(
            key,
            lambda: self._get_json(url),
            label=f"github_index.get_languages({full_name})",
        )
        if not isinstance(result, dict):
            return {}
        return {str(k): int(v) for k, v in result.items() if isinstance(v, (int, float))}

    def get_contributors(self, full_name: str, *, per_page: int = 100) -> list[dict[str, Any]]:
        url = f"{self._api_base}/repos/{full_name}/contributors?per_page={per_page}"
        key = ProviderCache.make_key(
            "github_index", "get_contributors", full_name=full_name, per_page=per_page,
        )
        result = self._cache.get_or_set(
            key,
            lambda: self._get_json(url),
            label=f"github_index.get_contributors({full_name})",
        )
        if not isinstance(result, list):
            return []
        return [c for c in result if isinstance(c, dict) and c.get("login")]

    def get_readme(self, full_name: str, *, max_bytes: int) -> tuple[str | None, str | None]:
        """Return (markdown_text, original_path) or (None, None) on absence/error.

        `path` is the original filename inside the repo (e.g. `README.md`,
        `docs/README.rst`) — useful for telemetry and logging.
        """
        url = f"{self._api_base}/repos/{full_name}/readme"
        key = ProviderCache.make_key("github_index", "get_readme", full_name=full_name)
        payload = self._cache.get_or_set(
            key,
            lambda: self._get_json(url),
            label=f"github_index.get_readme({full_name})",
        )
        if not isinstance(payload, dict):
            return None, None
        encoded = payload.get("content")
        if not isinstance(encoded, str):
            return None, payload.get("path")
        try:
            raw = base64.b64decode(encoded)
        except (ValueError, TypeError):
            LOGGER.warning("github README base64 decode failed: %s", full_name)
            return None, payload.get("path")
        if len(raw) > max_bytes:
            LOGGER.info(
                "github README truncated %d -> %d bytes for %s",
                len(raw),
                max_bytes,
                full_name,
            )
            raw = raw[:max_bytes]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return text, payload.get("path")
