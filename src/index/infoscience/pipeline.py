"""Query pipeline: filter → vector → rerank, with cross-entity joins.

Qdrant-backed.

`where` accepts a JSON-style dict of `{field: <operator-or-value>}`. Each
field's operator can be:

    * a scalar value (interpreted as ``$eq``)
    * a list (``$in``)
    * a dict with one of: ``$eq``, ``$ne``, ``$in``, ``$contains``,
      ``$gte`` and/or ``$lte``

Example: ``{"year": {"$gte": 2022}, "has_github_match": true,
"matched_urls": {"$contains": "huggingface.co"}}``.

Modes:
    * `hybrid` (default): vector top-K filtered → rerank → top-N
    * `vector-only`: vector top-K filtered, no rerank
    * `lexical`: scroll with payload filter only (Qdrant has no built-in
                 BM25; use a `$contains`-style filter for keyword search)
    * `filter-only`: structured predicates only, no scoring
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .config import InfoscienceIndexConfig
from .embed import RCPEmbedder
from .rerank import RCPReranker
from .store import (
    ARTICLES_COLLECTION,
    CHUNKS_COLLECTION,
    ORGANIZATIONS_COLLECTION,
    PERSONS_COLLECTION,
    QdrantStore,
    article_point_id,
    build_filter,
    organization_point_id,
    person_point_id,
)

logger = logging.getLogger(__name__)


TARGET_COLLECTIONS = {
    "chunks": CHUNKS_COLLECTION,
    "articles": ARTICLES_COLLECTION,
    "persons": PERSONS_COLLECTION,
    "organizations": ORGANIZATIONS_COLLECTION,
}

_DOC_FIELD = {
    CHUNKS_COLLECTION: "text",
    ARTICLES_COLLECTION: "abstract",
    PERSONS_COLLECTION: "biography",
    ORGANIZATIONS_COLLECTION: "description",
}


@dataclass
class QueryResult:
    target: str
    rows: List[Dict[str, Any]] = field(default_factory=list)
    related_persons: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    related_organizations: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


def _flatten(hits: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lift the payload one level so the caller doesn't have to dig."""
    out: List[Dict[str, Any]] = []
    for h in hits:
        row = dict(h.get("payload") or {})
        row["id"] = h.get("id")
        if "score" in h:
            row["score"] = h["score"]
        out.append(row)
    return out


async def _vector_search(
    cfg: InfoscienceIndexConfig,
    store: QdrantStore,
    collection: str,
    query: str,
    where: Optional[dict],
    top_k: int,
) -> List[Dict[str, Any]]:
    async with RCPEmbedder(cfg.rcp) as embedder:
        vec = await embedder.embed_query(query)
    hits = store.search(
        collection,
        query_vector=vec,
        top_k=top_k,
        query_filter=build_filter(where),
    )
    return _flatten(hits)


def _lexical_search(
    store: QdrantStore,
    collection: str,
    query: str,
    where: Optional[dict],
    top_k: int,
) -> List[Dict[str, Any]]:
    # Qdrant has no built-in BM25; "lexical" here means
    # filter-on-text-contains. Combine the user's where with a $contains
    # over the canonical text field for the target collection.
    text_field = _DOC_FIELD.get(collection, "text")
    payload = dict(where or {})
    payload[text_field] = {"$contains": query}
    hits = store.scroll(
        collection,
        query_filter=build_filter(payload),
        limit=top_k,
    )
    return _flatten(hits)


def _filter_only(
    store: QdrantStore,
    collection: str,
    where: Optional[dict],
    top_k: int,
) -> List[Dict[str, Any]]:
    hits = store.scroll(
        collection,
        query_filter=build_filter(where),
        limit=top_k,
    )
    return _flatten(hits)


async def _rerank(
    cfg: InfoscienceIndexConfig,
    query: str,
    rows: List[Dict[str, Any]],
    target_collection: str,
    top_n: int,
) -> List[Dict[str, Any]]:
    if not rows:
        return []
    doc_field = _DOC_FIELD.get(target_collection, "text")
    docs = [
        r.get(doc_field) or r.get("text") or r.get("title") or r.get("name") or ""
        for r in rows
    ]
    async with RCPReranker(cfg.rcp) as reranker:
        hits = await reranker.rerank(query, docs, top_n=top_n)
    if not hits:
        return rows[:top_n]
    return [rows[h.index] | {"rerank_score": h.score} for h in hits]


def _resolve_persons(store: QdrantStore, person_uuids: Sequence[str]) -> List[Dict[str, Any]]:
    if not person_uuids:
        return []
    ids = [person_point_id(u) for u in person_uuids]
    return _flatten(store.lookup(PERSONS_COLLECTION, ids=ids))


def _resolve_orgs(store: QdrantStore, org_uuids: Sequence[str]) -> List[Dict[str, Any]]:
    if not org_uuids:
        return []
    ids = [organization_point_id(u) for u in org_uuids]
    return _flatten(store.lookup(ORGANIZATIONS_COLLECTION, ids=ids))


def _row_key(row: Dict[str, Any]) -> str:
    """Pick a stable per-row key for cross-entity join indexing."""
    return (
        row.get("article_uuid")
        or row.get("person_uuid")
        or row.get("org_uuid")
        or row.get("chunk_id")
        or row.get("id")
        or ""
    )


async def query(
    cfg: InfoscienceIndexConfig,
    text: str,
    *,
    target: str = "chunks",
    where: Optional[dict] = None,
    top_k: int = 50,
    top_n: int = 10,
    mode: str = "hybrid",
    with_authors: bool = False,
    with_orgs: bool = False,
) -> QueryResult:
    if target not in TARGET_COLLECTIONS:
        msg = f"Unknown target {target!r}. Pick from {sorted(TARGET_COLLECTIONS)}."
        raise ValueError(msg)

    store = QdrantStore.from_config(cfg)
    name = TARGET_COLLECTIONS[target]
    if not store.client.collection_exists(name):
        msg = f"Collection {name!r} does not exist. Run `embed` first."
        raise ValueError(msg)

    if mode == "filter-only":
        rows = _filter_only(store, name, where, top_k)
    elif mode == "lexical":
        rows = _lexical_search(store, name, text, where, top_k)
    elif mode == "vector-only":
        rows = await _vector_search(cfg, store, name, text, where, top_k)
    elif mode == "hybrid":
        rows = await _vector_search(cfg, store, name, text, where, top_k)
        rows = await _rerank(cfg, text, rows, name, top_n)
    else:
        msg = f"Unknown mode {mode!r}."
        raise ValueError(msg)

    result = QueryResult(target=target, rows=rows)
    if with_authors:
        for row in rows:
            uuids = row.get("author_uuids") or []
            row_key = _row_key(row)
            if uuids and row_key:
                result.related_persons[row_key] = _resolve_persons(store, uuids)
    if with_orgs:
        for row in rows:
            uuids = row.get("org_uuids") or []
            row_key = _row_key(row)
            if uuids and row_key:
                result.related_organizations[row_key] = _resolve_orgs(store, uuids)
    return result
