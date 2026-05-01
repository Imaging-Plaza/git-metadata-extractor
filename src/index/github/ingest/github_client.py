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

The cache TTL matches the v2 default (30 days; configurable via
`V2_PROVIDER_CACHE_TTL_DAYS`). Cache hits are silent log lines so a cold
re-run re-uses the data without a single REST call.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import requests

from src.v2.ingest.cache import ProviderCache

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30


class GitHubClient:
    """Thin REST client with shared `ProviderCache` for the github index."""

    def __init__(
        self,
        *,
        api_base: str,
        token: str | None,
        cache_path: Path,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._token = (token or "").strip() or None
        self._cache = ProviderCache(cache_path)

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json"}
        if self._token:
            h["Authorization"] = f"token {self._token}"
        return h

    def _get_json(self, url: str) -> Any:
        try:
            response = requests.get(
                url,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            LOGGER.exception("github GET failed: %s", url)
            return None
        if response.status_code == 404:
            LOGGER.info("github GET 404: %s", url)
            return None
        if response.status_code != 200:
            LOGGER.warning(
                "github GET returned %d for %s", response.status_code, url,
            )
            return None
        try:
            return response.json()
        except ValueError:
            LOGGER.exception("github GET response not JSON: %s", url)
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
