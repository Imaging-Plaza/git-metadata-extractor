"""Reverse index from HuggingFace `repo_id` → citing Infoscience articles.

Mines `data/index/infoscience/dumps/infoscience_links_index.json` for every
URL whose host is `huggingface.co` or `hf.co`, parses out the `<author>` and
`<author>/<repo>` segments, and exposes:

  - `articles_for_repo(repo_id)`  — papers citing `<author>/<repo>`
  - `articles_for_author(author)` — papers citing any repo under `<author>`

The full index file is ~8 MB and parsed lazily on first call; subsequent
calls reuse the in-memory map. If the file is missing (infoscience index
not built locally), the module returns empty lists and never raises.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Default location — overridable via param if the user moves things around.
DEFAULT_INDEX_PATH = Path("data/index/infoscience/dumps/infoscience_links_index.json")

# Path segments that aren't namespaces themselves but precede one.
_NON_NAMESPACE_PREFIXES = frozenset(
    {"datasets", "models", "spaces", "papers", "collections"},
)

_HF_URL_RE = re.compile(
    r"https?://(?:huggingface\.co|hf\.co)/([^/\s?#]+)(?:/([^/\s?#]+))?",
    re.IGNORECASE,
)

# Module-level cache. None == not loaded yet; {} == loaded but file missing.
_BY_REPO: dict[str, list[dict[str, Any]]] | None = None
_BY_AUTHOR: dict[str, list[dict[str, Any]]] | None = None
_LOADED_FROM: Path | None = None


def _parse_namespace(url: str) -> tuple[str | None, str | None]:
    """Return `(author, repo_id)` from a HF URL, or `(None, None)` on no match.

    Skips `huggingface.co/datasets/foo/bar` style — the leading `datasets`
    isn't an author.
    """
    m = _HF_URL_RE.search(url)
    if not m:
        return (None, None)
    first, second = m.group(1), m.group(2)
    if first.lower() in _NON_NAMESPACE_PREFIXES:
        # `huggingface.co/datasets/<author>/<repo>` — promote one level.
        if not second:
            return (None, None)
        # Need to look further into the URL for the next segment after `second`.
        tail = url[m.end():]
        tail_m = re.match(r"/([^/\s?#]+)", tail)
        author = second
        repo = tail_m.group(1) if tail_m else None
        return (author, f"{author}/{repo}" if repo else None)
    return (first, f"{first}/{second}" if second else None)


def _build_indexes(path: Path) -> None:
    """Parse the slim index → populate `_BY_REPO` and `_BY_AUTHOR`."""
    global _BY_REPO, _BY_AUTHOR, _LOADED_FROM
    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_author: dict[str, list[dict[str, Any]]] = defaultdict(list)

    if not path.exists():
        LOGGER.info("infoscience links index not present at %s; cross-links disabled", path)
        _BY_REPO, _BY_AUTHOR, _LOADED_FROM = {}, {}, path
        return

    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    seen_per_article: dict[str, set[str]] = defaultdict(set)
    for item in data.get("items", []):
        body = item.get("body_urls") or {}
        urls: list[str] = []
        for key in ("huggingface", "hf_co"):
            urls.extend(body.get(key, []) or [])
        if not urls:
            continue
        article_ref = {
            "uuid": item.get("uuid"),
            "title": item.get("title"),
            "year": item.get("year"),
            "doi": item.get("doi"),
            "infoscience_url": item.get("infoscience_url"),
        }
        article_uuid = item.get("uuid") or ""
        for url in urls:
            author, repo_id = _parse_namespace(url)
            if author and f"author:{author}" not in seen_per_article[article_uuid]:
                by_author[author].append(article_ref)
                seen_per_article[article_uuid].add(f"author:{author}")
            if repo_id and f"repo:{repo_id}" not in seen_per_article[article_uuid]:
                by_repo[repo_id].append(article_ref)
                seen_per_article[article_uuid].add(f"repo:{repo_id}")

    _BY_REPO = dict(by_repo)
    _BY_AUTHOR = dict(by_author)
    _LOADED_FROM = path
    LOGGER.info(
        "infoscience links index loaded from %s: %d repos, %d authors",
        path,
        len(_BY_REPO),
        len(_BY_AUTHOR),
    )


def _ensure_loaded(index_path: Path | None = None) -> None:
    """Lazy-load on first call; reload only if the path argument changes."""
    target = index_path or DEFAULT_INDEX_PATH
    if _BY_REPO is None or _LOADED_FROM != target:
        _build_indexes(target)


def articles_for_repo(
    repo_id: str,
    *,
    index_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return Infoscience articles citing `<author>/<repo>` (case-insensitive on author)."""
    _ensure_loaded(index_path)
    assert _BY_REPO is not None
    if repo_id in _BY_REPO:
        return _BY_REPO[repo_id]
    # Tolerate case differences in the namespace half (HF is case-insensitive
    # on lookup but case-preserving on the canonical name).
    author, _, name = repo_id.partition("/")
    if not name:
        return []
    needle = f"{author.lower()}/{name.lower()}"
    for key, refs in _BY_REPO.items():
        if key.lower() == needle:
            return refs
    return []


def articles_for_author(
    author: str,
    *,
    index_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return Infoscience articles citing any repo under `author` (case-insensitive)."""
    _ensure_loaded(index_path)
    assert _BY_AUTHOR is not None
    if author in _BY_AUTHOR:
        return _BY_AUTHOR[author]
    needle = author.lower()
    for key, refs in _BY_AUTHOR.items():
        if key.lower() == needle:
            return refs
    return []


def reset_cache() -> None:
    """Drop the in-memory index. Useful in tests."""
    global _BY_REPO, _BY_AUTHOR, _LOADED_FROM
    _BY_REPO = None
    _BY_AUTHOR = None
    _LOADED_FROM = None


__all__ = ["articles_for_repo", "articles_for_author", "reset_cache"]
