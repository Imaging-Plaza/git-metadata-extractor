"""Extract canonical GitHub URLs from persisted Work abstracts.

Reuses `src.v2.ingest.detection.github_url_classifier.classify_github_url`
so the test-set URLs are normalized identically to what v2 expects on its
`/extract` endpoint.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from src.v2.ingest.detection.github_url_classifier import classify_github_url
from src.v2.ingest.detection.models import UnsupportedGitHubURL

if TYPE_CHECKING:
    from src.index.openalex.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)

# Match URLs starting with http(s):// containing a github.com host. The
# negative lookahead on trailing punctuation is intentional — academic
# abstracts often include URLs followed by ".", ",", ")".
_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?github\.com/[^\s<>\"')\]]+",
    flags=re.IGNORECASE,
)


def extract_github_urls(text: str) -> list[str]:
    """Return raw URL substrings found in `text` (no canonicalization)."""
    if not text:
        return []
    raw = _URL_PATTERN.findall(text)
    cleaned: list[str] = []
    for url in raw:
        # Strip trailing punctuation that often hugs URLs in prose.
        stripped = url.rstrip(".,;:)]}>\"'")
        if stripped:
            cleaned.append(stripped)
    return cleaned


def _classify_safe(url: str) -> tuple[str, str | None, str | None] | None:
    """Best-effort classification.

    Returns (normalized_url, owner, repo) or None if the URL is unusable
    even via `UnsupportedGitHubURL` (e.g., not a github.com URL at all).
    """
    try:
        result = classify_github_url(url)
    except UnsupportedGitHubURL as exc:
        # An issue / PR / blob URL — we still capture the normalized
        # repository URL since that's what v2 can act on.
        return (exc.normalized_url, None, None)
    except ValueError as exc:
        LOGGER.debug("skipping unclassifiable URL %s: %s", url, exc)
        return None
    return (result.normalized_url, result.owner, result.repo)


def extract_and_persist_for_work(
    store: DuckDBStore,
    *,
    work_id: str,
    text: str,
    source: str,
) -> int:
    """Scan `text`, persist matches against `work_id`. Return count persisted."""
    if source not in ("abstract", "fulltext"):
        message = f"Invalid source: {source}"
        raise ValueError(message)
    persisted = 0
    seen_norm: set[str] = set()
    for url in extract_github_urls(text):
        classified = _classify_safe(url)
        if classified is None:
            continue
        normalized, owner, repo = classified
        if normalized in seen_norm:
            continue
        seen_norm.add(normalized)
        store.upsert_github_url(
            work_id=work_id,
            url=url,
            normalized_url=normalized,
            owner=owner,
            repo=repo,
            source=source,
        )
        persisted += 1
    return persisted


def extract_for_persisted_works(store: DuckDBStore) -> tuple[int, int]:
    """Sweep over all `works` rows and extract abstract URLs.

    Returns (works_scanned, urls_persisted).
    """
    # Materialize the full SELECT before iterating: the upsert calls inside
    # the loop reuse the same DuckDB connection and would clobber a live
    # cursor mid-stream (DuckDB has one cursor per connection).
    rows = store.connect().execute(
        "SELECT openalex_id, abstract FROM works WHERE abstract IS NOT NULL",
    ).fetchall()
    scanned = 0
    total_persisted = 0
    for work_id, abstract in rows:
        scanned += 1
        if not abstract:
            continue
        total_persisted += extract_and_persist_for_work(
            store,
            work_id=work_id,
            text=abstract,
            source="abstract",
        )
    LOGGER.info(
        "github-extract complete: scanned=%d urls_persisted=%d",
        scanned,
        total_persisted,
    )
    return scanned, total_persisted
