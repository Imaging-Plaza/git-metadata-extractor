"""EPFL Graph ontology endpoints (concept → discipline / category).

The upstream ``graphai-client`` library does not wrap the ``/ontology/*``
namespace, so we hit the API directly using the cached login_info from
``src.module.epfl_graph.auth``. Three helpers are exposed:

- :func:`concept_nearest_categories` — POST /ontology/nearest_neighbor/concept/category
- :func:`ontology_tree` — GET  /ontology/tree (cached for the process lifetime)
- :func:`category_info` — GET  /ontology/tree/category/{category_id}

The category IDs are slugs (``topics-in-natural-language-processing``,
``computer-graphics``, …). The tree exposes a ``child_to_parent`` map so
callers can walk upward to broader disciplines.
"""

from __future__ import annotations

import threading
from typing import Any

import requests

from src.module.epfl_graph.auth import get_login_info

DEFAULT_TIMEOUT = 60.0
_TREE_LOCK = threading.Lock()
_TREE_CACHE: dict[str, Any] | None = None

_CATEGORY_INFO_LOCK = threading.Lock()
_CATEGORY_INFO_CACHE: dict[str, dict[str, Any]] = {}

_OPENALEX_TOPICS_LOCK = threading.Lock()
_OPENALEX_TOPICS_CACHE: dict[str, list[dict[str, Any]]] = {}


def _bearer_headers(login_info: dict[str, Any] | None) -> tuple[str, dict[str, str]]:
    info = login_info or get_login_info()
    return info["host"], {"Authorization": f"Bearer {info['token']}"}


def concept_nearest_categories(  # noqa: PLR0913
    concept_id: str | int,
    *,
    top_n: int = 5,
    use_embeddings: bool = False,
    top_down_search: bool = True,
    return_clusters: bool = False,
    login_info: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Return the top-N EPFL ontology categories for a Wikipedia concept ID.

    Each entry has shape ``{"category_id": str, "score": float, "rank": int,
    "clusters": list | None}``. Returns an empty list when the API rejects
    the concept (typically because it isn't mapped in the EPFL ontology).
    """
    host, headers = _bearer_headers(login_info)
    body = {
        "src": str(concept_id),
        "top_n": top_n,
        "use_embeddings": use_embeddings,
        "top_down_search": top_down_search,
        "return_clusters": return_clusters,
    }
    try:
        response = requests.post(
            host + "/ontology/nearest_neighbor/concept/category",
            headers={**headers, "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
    except requests.RequestException:
        return []
    if not response.ok:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    scores = payload.get("scores") if isinstance(payload, dict) else None
    return scores if isinstance(scores, list) else []


def ontology_tree(
    *,
    login_info: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    refresh: bool = False,
) -> dict[str, Any]:
    """Fetch the ``child_to_parent`` ontology tree, cached for the process lifetime."""
    global _TREE_CACHE  # noqa: PLW0603
    with _TREE_LOCK:
        if _TREE_CACHE is not None and not refresh:
            return _TREE_CACHE
        host, headers = _bearer_headers(login_info)
        try:
            response = requests.get(
                host + "/ontology/tree", headers=headers, timeout=timeout,
            )
            response.raise_for_status()
            _TREE_CACHE = response.json() or {}
        except (requests.RequestException, ValueError):
            _TREE_CACHE = {}
        return _TREE_CACHE


def category_info(
    category_id: str,
    *,
    login_info: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Fetch metadata for a single ontology category (cached per process).

    Returns ``{}`` when the API call fails or the category is unknown.
    """
    cid = str(category_id)
    if use_cache:
        cached = _CATEGORY_INFO_CACHE.get(cid)
        if cached is not None:
            return cached
    host, headers = _bearer_headers(login_info)
    try:
        response = requests.get(
            host + "/ontology/tree/category/" + cid,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json() or {}
    except (requests.RequestException, ValueError):
        payload = {}
    if use_cache:
        with _CATEGORY_INFO_LOCK:
            _CATEGORY_INFO_CACHE[cid] = payload
    return payload


def category_wikipedia(category_id: str) -> dict[str, Any]:
    """Return ``{name, wikipedia_page_id, wikipedia_url}`` for a category.

    Pulls from the cached :func:`category_info` response. Falls back to
    the slug-derived label when the API call fails.
    """
    info_payload = category_info(category_id) or {}
    info_block = info_payload.get("info") if isinstance(info_payload, dict) else None
    name: str | None = None
    page_id: str | None = None
    if isinstance(info_block, dict):
        if isinstance(info_block.get("name"), str) and info_block["name"].strip():
            name = info_block["name"].strip()
        raw_id = info_block.get("id")
        if isinstance(raw_id, (int, str)) and str(raw_id).strip():
            page_id = str(raw_id).strip()
    name_or_label = name or category_id_to_label(category_id)
    wikipedia_url: str | None = None
    if page_id:
        wikipedia_url = "https://en.wikipedia.org/?curid=" + page_id
    elif name_or_label:
        wikipedia_url = (
            "https://en.wikipedia.org/wiki/" + name_or_label.replace(" ", "_")
        )
    return {
        "name": name_or_label,
        "wikipedia_page_id": page_id,
        "wikipedia_url": wikipedia_url,
    }


def category_nearest_openalex_topics(
    category_id: str,
    *,
    top_n: int = 5,
    login_info: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Return OpenAlex topics nearest to an EPFL category.

    Each entry has ``{topic_id, topic_name, score, embedding_score,
    wikipedia_score}``. Results are cached per ``(category_id, top_n)``
    for the lifetime of the process.
    """
    cid = str(category_id)
    cache_key = f"{cid}|{top_n}"
    if use_cache:
        cached = _OPENALEX_TOPICS_CACHE.get(cache_key)
        if cached is not None:
            return cached
    host, headers = _bearer_headers(login_info)
    try:
        response = requests.get(
            host + "/ontology/openalex/category/" + cid + "/nearest_topics",
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        payload = []
    topics: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload[:top_n]:
            if not isinstance(item, dict):
                continue
            topic_id = item.get("topic_id")
            topic_name = item.get("topic_name")
            if not isinstance(topic_id, (str, int)) or not str(topic_id).strip():
                continue
            topics.append(
                {
                    "topic_id": str(topic_id).strip(),
                    "topic_name": (
                        topic_name.strip()
                        if isinstance(topic_name, str)
                        else None
                    ),
                    "score": item.get("score"),
                    "embedding_score": item.get("embedding_score"),
                    "wikipedia_score": item.get("wikipedia_score"),
                },
            )
    if use_cache:
        with _OPENALEX_TOPICS_LOCK:
            _OPENALEX_TOPICS_CACHE[cache_key] = topics
    return topics


def category_id_to_label(category_id: str) -> str:
    """Cheap human-form for a slug-style category id.

    ``topics-in-natural-language-processing`` →
    ``Topics in Natural Language Processing``. Articles/prepositions stay
    lowercase. Fall back to the original slug when in doubt.
    """
    if not isinstance(category_id, str) or not category_id.strip():
        return ""
    parts = category_id.strip().split("-")
    minor = {"in", "of", "and", "or", "the", "a", "an", "for", "on", "to", "with"}
    out: list[str] = []
    for index, part in enumerate(parts):
        if not part:
            continue
        if index > 0 and part.lower() in minor:
            out.append(part.lower())
        else:
            out.append(part[:1].upper() + part[1:])
    return " ".join(out) if out else category_id


GRAPHSEARCH_CATEGORY_URL = "https://graphsearch.epfl.ch/en/category/"
ROOT_CATEGORY_ID = "root"


def category_graphsearch_url(category_id: str) -> str:
    """User-facing GraphSearch landing page for a category slug."""
    return GRAPHSEARCH_CATEGORY_URL + str(category_id)


def category_chain(
    category_id: str,
    *,
    login_info: dict[str, Any] | None = None,
    include_root: bool = False,
    max_depth: int = 20,
) -> list[str]:
    """Return the parent chain from ``category_id`` upward to the root.

    The first element is always ``category_id`` itself. ``root`` is omitted
    by default (it carries no semantic info). Returns a single-element list
    if the category isn't in the tree.
    """
    tree = ontology_tree(login_info=login_info)
    edges = tree.get("child_to_parent") if isinstance(tree, dict) else None
    if not isinstance(edges, list):
        return [category_id]
    parent_map = {
        edge["child_id"]: edge["parent_id"]
        for edge in edges
        if isinstance(edge, dict)
        and isinstance(edge.get("child_id"), str)
        and isinstance(edge.get("parent_id"), str)
    }
    chain: list[str] = [category_id]
    node = category_id
    seen = {category_id}
    while node in parent_map and len(chain) < max_depth:
        parent = parent_map[node]
        if parent in seen:
            break
        if not include_root and parent == ROOT_CATEGORY_ID:
            break
        chain.append(parent)
        seen.add(parent)
        node = parent
    return chain
