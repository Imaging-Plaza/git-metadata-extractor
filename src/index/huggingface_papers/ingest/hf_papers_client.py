"""Direct HF Papers REST client.

The HF Papers surface (https://huggingface.co/papers) is exposed via
``GET /api/papers/{arxiv_id}``. The existing ``huggingface_hub`` Python
library doesn't wrap this endpoint, so we make plain HTTP calls
with the same `ProviderCache`-backed retry shape the github-index
client uses.

Anonymous access works for public papers (the common case). Optional
``HF_TOKEN`` is included as a ``Bearer`` header when set — required
for org-gated previews and useful to avoid rate limits.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from src.v2.ingest.cache import ProviderCache

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30
RATE_LIMIT_SLEEP_PADDING_SECONDS = 5

# arXiv id pattern: YYMM.NNNNN (post-2007 scheme). Older papers used a
# subject-class prefix (e.g. `cs.LG/0701234`) — we accept those too but
# strip them when persisting (we use the modern numeric id as the
# primary key when both shapes resolve to the same paper).
_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$", re.IGNORECASE)


def normalize_arxiv_id(raw: Any) -> str | None:
    """Strip whitespace + optional trailing version suffix.

    Accepts:
      - bare modern ids: `2310.01234`, `2310.01234v2`
      - URL forms: `https://arxiv.org/abs/2310.01234`,
        `https://huggingface.co/papers/2310.01234`
      - arxiv DOIs: `10.48550/arXiv.2310.01234`
    Returns the bare modern id without version, or None on garbage.
    """
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    lower = candidate.lower()
    for prefix in (
        "https://huggingface.co/papers/",
        "http://huggingface.co/papers/",
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
        "https://doi.org/10.48550/arxiv.",
        "http://doi.org/10.48550/arxiv.",
        "10.48550/arxiv.",
        "arxiv:",
    ):
        if lower.startswith(prefix):
            candidate = candidate[len(prefix):]
            break
    # Strip a trailing `.pdf` if the URL was a PDF link.
    if candidate.lower().endswith(".pdf"):
        candidate = candidate[:-4]
    # Strip a trailing version suffix (`v1`, `v2`, …) so the row is
    # stable across new versions of the same paper.
    candidate = _ARXIV_VERSION_SUFFIX.sub("", candidate)
    candidate = candidate.strip("/")
    if not candidate:
        return None
    return candidate


class HFPapersClient:
    """Thin REST client for `GET /api/papers/{arxiv_id}` with shared
    `ProviderCache` (same TTL as the github-index client)."""

    def __init__(
        self,
        *,
        api_base: str,
        token: str | None,
        cache_path: Path,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        token_stripped = (token or "").strip()
        self._token: str | None = token_stripped or None
        self._cache = ProviderCache(cache_path)
        if self._token:
            LOGGER.info("hf_papers client: token configured")
        else:
            LOGGER.info("hf_papers client: anonymous (public papers only)")

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _get_json(self, url: str) -> Any:
        try:
            response = requests.get(
                url,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            LOGGER.exception("hf_papers GET failed: %s", url)
            return None
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                LOGGER.exception("hf_papers GET response not JSON: %s", url)
                return None
        if response.status_code == 404:
            LOGGER.info("hf_papers GET 404: %s", url)
            return None
        if response.status_code == 429:
            # No multi-token rotation here; rate limits on HF Papers
            # are generous in practice. Surface a warning and bail.
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 60.0
            except ValueError:
                delay = 60.0
            LOGGER.warning(
                "hf_papers rate-limited; sleeping %.0fs", delay,
            )
            time.sleep(delay + RATE_LIMIT_SLEEP_PADDING_SECONDS)
            return None
        LOGGER.warning(
            "hf_papers GET returned %d for %s",
            response.status_code,
            url,
        )
        return None

    def get_paper(self, arxiv_id: str) -> dict[str, Any] | None:
        """Fetch one paper card. The `arxiv_id` is the canonical id
        without version suffix (use `normalize_arxiv_id` to massage
        wire input first)."""
        url = f"{self._api_base}/api/papers/{arxiv_id}"
        key = ProviderCache.make_key(
            "hf_papers_index", "get_paper", arxiv_id=arxiv_id,
        )
        return self._cache.get_or_set(
            key,
            lambda: self._get_json(url),
            label=f"hf_papers_index.get_paper({arxiv_id})",
        )
