"""Async RAG provider over the HuggingFace Qdrant index.

Wraps :class:`src.index.huggingface.vector.qdrant_store.QdrantStore` with
:class:`src.index.huggingface.embed.rcp_client.RCPEmbeddingClient` and
:class:`src.index.huggingface.rerank.rcp_client.RCPRerankerClient`.

Exposes one method:

* ``search(query, *, collection, top_k, filters, rerank)`` —
  collection ∈ {"models", "datasets", "spaces"}.

Graceful degradation: connection / RCP failure → ``[]`` + warning.

HF chunk payloads contain only metadata (no body text). When ``rerank=True``
the provider synthesises a rerank document string from the available
identifier fields (``repo_id``, ``library_name``, ``pipeline_tag``, ``sdk``).
This is a weak signal; rerank is most useful here for ambiguous repo-name
disambiguation rather than full-text relevance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from src.index._rcp.embed_client import (
    RCPEmbeddingClient,
    RCPEmbeddingError,
)
from src.index._rcp.reranker_client import RCPRerankerClient
from src.index.openalex.vector.qdrant_store import QdrantStore
from src.v2.ingest.providers._rag_helpers import (
    apply_rerank_indices,
    env_enabled,
    expand_candidate_k,
    filter_allowlist,
    safe_rerank_documents,
    thin_payload,
    to_simple_filter_payload,
)

if TYPE_CHECKING:
    from src.index._huggingface_base.config_base import (
        HFEntityIndexConfigBase as HuggingFaceIndexConfig,
    )

logger = logging.getLogger(__name__)

CollectionName = Literal["models", "datasets", "spaces", "orgs"]


async def lineage(
    repo_id: str,
    *,
    depth: int = 3,
) -> dict[str, Any]:
    """Walk the HuggingFace ``base_models`` graph from ``repo_id``.

    Returns ancestors (parent models), descendants (models fine-tuned
    from ``repo_id``), and the explicit edge list. Pure local DuckDB
    lookup against the ``huggingface_models`` store — no RCP / Qdrant
    calls. Cheap (sub-second).

    J1: re-implemented against the per-entity HuggingFaceModelsStore
    after H7 retired the legacy catch-all module. Same output shape as
    the legacy version so downstream consumers don't need to change.
    """
    if not isinstance(repo_id, str) or not repo_id.strip():
        return {
            "root": repo_id,
            "ancestors": {},
            "descendants": {},
            "edges": [],
            "depth": depth,
        }
    try:
        from src.index.huggingface_models.retrieval.lineage import (
            compute_lineage as _compute,
        )
        from src.index.huggingface_models.storage.duckdb_store import (
            HuggingFaceModelsStore as _Store,
        )
        return await asyncio.to_thread(_walk_lineage, repo_id, depth, _compute, _Store)
    except Exception as exc:  # noqa: BLE001
        logger.warning("huggingface.rag.lineage(%r) failed — %s", repo_id, exc)
        return {
            "root": repo_id,
            "ancestors": {},
            "descendants": {},
            "edges": [],
            "depth": depth,
        }


def _walk_lineage(
    repo_id: str, depth: int, compute_fn: Any, store_cls: Any,
) -> dict[str, Any]:
    """Open the store and delegate. Kept as a sync helper so
    `asyncio.to_thread` has something to wrap."""
    store = store_cls.open()
    try:
        return compute_fn(repo_id, store=store, depth=depth)
    finally:
        store.close()


# Maps the LLM-facing collection name to the actual Qdrant collection.
# Updated for the H7 split: the legacy `hf_*` collection names are
# replaced by `huggingface_*`, and the catch-all `orgs` collection
# splits into `huggingface_users` + `huggingface_organizations`.
# Note: this map is keyed on the LLM-facing logical name, so we
# keep `"orgs"` as a logical key but route it to the organizations
# collection by default (the more common case for org-shaped queries).
_HF_COLLECTION_MAP: dict[str, str] = {
    "models": "huggingface_models",
    "datasets": "huggingface_datasets",
    "spaces": "huggingface_spaces",
    "orgs": "huggingface_organizations",
    "users": "huggingface_users",
}

_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "library_name",
    "pipeline_tag",
    "sdk",
    "gated",
    "downloads",
    "downloads_all_time",
    "likes",
    "author",
    "license",
    "entity_type",
})

_THIN_KEYS_MODELS: tuple[str, ...] = (
    "repo_id", "author", "library_name", "pipeline_tag", "license",
    "downloads", "downloads_all_time", "likes", "gated", "last_modified",
)
_THIN_KEYS_DATASETS: tuple[str, ...] = (
    "repo_id", "author", "license", "downloads", "downloads_all_time",
    "likes", "gated", "last_modified",
)
_THIN_KEYS_SPACES: tuple[str, ...] = (
    "repo_id", "author", "sdk", "license", "likes", "last_modified",
)

_THIN_KEYS_ORGS: tuple[str, ...] = (
    "repo_id", "slug", "fullname", "namespace_kind", "scope",
    "num_models", "num_datasets", "num_spaces", "num_followers",
)

_THIN_KEYS_FOR: dict[str, tuple[str, ...]] = {
    "models": _THIN_KEYS_MODELS,
    "datasets": _THIN_KEYS_DATASETS,
    "spaces": _THIN_KEYS_SPACES,
    "orgs": _THIN_KEYS_ORGS,
}

_DEFAULT_TOP_K = 10
_LOG_LABEL = "huggingface_rag"


def _rerank_text(collection: str, payload: dict[str, Any]) -> str:
    """Synthesise a short doc string for rerank from HF metadata fields."""
    parts: list[str] = []
    repo_id = payload.get("repo_id")
    if isinstance(repo_id, str) and repo_id:
        parts.append(repo_id)
    if collection == "models":
        for key in ("library_name", "pipeline_tag"):
            v = payload.get(key)
            if isinstance(v, str) and v:
                parts.append(v)
    elif collection == "spaces":
        sdk = payload.get("sdk")
        if isinstance(sdk, str) and sdk:
            parts.append(sdk)
    return " ".join(parts)


class HuggingFaceRagProvider:
    """Async wrapper around the HuggingFace Qdrant index."""

    def __init__(
        self,
        *,
        store: QdrantStore,
        embedder: RCPEmbeddingClient,
        reranker: RCPRerankerClient | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._reranker = reranker

    @classmethod
    def from_config(
        cls,
        cfg: HuggingFaceIndexConfig,
    ) -> HuggingFaceRagProvider:
        return cls(
            store=QdrantStore(cfg),
            embedder=RCPEmbeddingClient(cfg),
            reranker=RCPRerankerClient(cfg),
        )

    async def lineage(
        self,
        repo_id: str,
        *,
        depth: int = 3,
    ) -> dict[str, Any]:
        """Walk the HF base_models DAG. Pure local DuckDB; no RCP."""
        return await lineage(repo_id, depth=depth)

    async def search(  # noqa: PLR0911 — each early return is a graceful-fail check
        self,
        query: str,
        *,
        collection: CollectionName = "models",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        qdrant_collection = _HF_COLLECTION_MAP.get(collection)
        if qdrant_collection is None:
            logger.warning("%s: unknown collection %r", _LOG_LABEL, collection)
            return []
        # Skip when missing — upstream `QdrantStore.search` would lazily
        # create an empty collection otherwise.
        if not self._store.client.collection_exists(qdrant_collection):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                _LOG_LABEL, qdrant_collection,
            )
            return []

        cleaned = filter_allowlist(filters, _ALLOWED_FILTER_KEYS, log_label=_LOG_LABEL)
        filter_payload = to_simple_filter_payload(cleaned)

        try:
            vectors = await self._embedder.embed_all([query])
        except (RCPEmbeddingError, Exception) as exc:  # noqa: BLE001 — RCP transport errors must not bubble
            logger.warning("%s: embed failed — %s", _LOG_LABEL, exc)
            return []
        if not vectors:
            return []
        vector = list(vectors[0])

        candidate_k = expand_candidate_k(top_k) if rerank else top_k

        try:
            hits = await asyncio.to_thread(
                self._store.search,
                qdrant_collection,
                query_vector=vector,
                top_k=candidate_k,
                filter_payload=filter_payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s: qdrant search failed (collection=%s) — %s",
                _LOG_LABEL, qdrant_collection, exc,
            )
            return []

        if rerank and hits and self._reranker is not None:
            hits = await self._maybe_rerank(query, collection, hits, top_k=top_k)
        else:
            hits = hits[:top_k]

        thin_keys = _THIN_KEYS_FOR[collection]
        return [
            thin_payload(
                hit.get("payload") or {},
                thin_keys,
                extras={
                    "id": hit.get("id"),
                    "score": hit.get("score"),
                    "collection": collection,
                },
            )
            for hit in hits
        ]

    async def _maybe_rerank(
        self,
        query: str,
        collection: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        documents = safe_rerank_documents(
            [_rerank_text(collection, h.get("payload") or {}) for h in hits],
        )
        try:
            results = await self._reranker.rerank(query, documents, top_n=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: rerank failed — %s", _LOG_LABEL, exc)
            return hits[:top_k]
        return apply_rerank_indices(hits, results, top_k=top_k)


def build_default_provider(
    cfg: HuggingFaceIndexConfig | None = None,
) -> HuggingFaceRagProvider | None:
    """Construct the default RAG provider, or return ``None`` on failure."""
    if not env_enabled("V2_HUGGINGFACE_RAG_ENABLED"):
        return None
    try:
        # Lazy load: missing yaml / env should not break v2 init.
        from src.index.huggingface.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return HuggingFaceRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = [
    "CollectionName",
    "HuggingFaceRagProvider",
    "build_default_provider",
]
