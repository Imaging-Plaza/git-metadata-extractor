"""Async RAG provider over the GitHub Qdrant index.

Single collection: ``github_repos``. Reuses OpenAlex's embedder, reranker
and ``QdrantStore`` via duck typing — ``GitHubIndexConfig`` mirrors the
same ``rcp.*`` / ``qdrant.*`` shape OpenAlex's clients expect.

Source corpus is `src/index/github/`: a deterministic GitHub-REST-fed
index of EPFL/Swiss-research repos (metadata + README chunks) embedded
with Qwen3-Embedding-8B. Useful when the agent needs to find repos
similar to the one it's processing — e.g. "are there other EPFL repos
implementing this technique?" or "what's the canonical fork?".
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from open_pulse_sources.index.github_repos.embed.pipeline import GITHUB_REPOS_COLLECTION
from open_pulse_sources.index._rcp.embed_client import (
    RCPEmbeddingClient,
    RCPEmbeddingError,
)
from open_pulse_sources.index._rcp.reranker_client import RCPRerankerClient
from open_pulse_sources.index.openalex.vector.qdrant_store import QdrantStore
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
    from open_pulse_sources.index.github_repos.config import GitHubIndexConfig

logger = logging.getLogger(__name__)

_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset({
    "entity_type",          # always "repos" for now; kept for federated symmetry
    "repo_id",
    "owner",
    "primary_language",
    "license_spdx",
    "is_archived",
    "is_fork",
})

_THIN_KEYS: tuple[str, ...] = (
    "repo_id", "owner", "name", "primary_language", "license_spdx",
    "stars", "forks", "pushed_at", "is_archived",
)

_DEFAULT_TOP_K = 10
_LOG_LABEL = "github_rag"


def _rerank_text(payload: dict[str, Any]) -> str:
    """Build the document fed to the cross-encoder reranker.

    The full README isn't in the payload (it's chunked across many points);
    `repo_id + description` is what we have on every hit and gives the
    reranker enough signal to compare candidates.
    """
    repo_id = payload.get("repo_id") or ""
    desc = payload.get("description") or ""
    return f"{repo_id}\n{desc}".strip() or repo_id


class GitHubRagProvider:
    """Async wrapper around the GitHub Qdrant index."""

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
    def from_config(cls, cfg: GitHubIndexConfig) -> GitHubRagProvider:
        # Duck-type into OpenAlex's clients — config shape matches.
        return cls(
            store=QdrantStore(cfg),  # type: ignore[arg-type]
            embedder=RCPEmbeddingClient(cfg),  # type: ignore[arg-type]
            reranker=RCPRerankerClient(cfg),  # type: ignore[arg-type]
        )

    async def search(
        self,
        query: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            return []
        if not self._store.client.collection_exists(GITHUB_REPOS_COLLECTION):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                _LOG_LABEL, GITHUB_REPOS_COLLECTION,
            )
            return []

        cleaned = filter_allowlist(filters, _ALLOWED_FILTER_KEYS, log_label=_LOG_LABEL)
        filter_payload = to_simple_filter_payload(cleaned)

        try:
            vectors = await self._embedder.embed_all([query])
        except (RCPEmbeddingError, Exception) as exc:  # noqa: BLE001
            logger.warning("%s: embed failed — %s", _LOG_LABEL, exc)
            return []
        if not vectors:
            return []
        vector = list(vectors[0])

        candidate_k = expand_candidate_k(top_k) if rerank else top_k

        try:
            hits = await asyncio.to_thread(
                self._store.search,
                GITHUB_REPOS_COLLECTION,
                query_vector=vector,
                top_k=candidate_k,
                filter_payload=filter_payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: qdrant search failed — %s", _LOG_LABEL, exc)
            return []

        if rerank and hits and self._reranker is not None:
            hits = await self._maybe_rerank(query, hits, top_k=top_k)
        else:
            hits = hits[:top_k]

        return [
            thin_payload(
                hit.get("payload") or {},
                _THIN_KEYS,
                extras={
                    "id": hit.get("id"),
                    "score": hit.get("score"),
                    "collection": GITHUB_REPOS_COLLECTION,
                },
            )
            for hit in hits
        ]

    async def _maybe_rerank(
        self,
        query: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        documents = safe_rerank_documents(
            [_rerank_text(h.get("payload") or {}) for h in hits],
        )
        try:
            results = await self._reranker.rerank(query, documents, top_n=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: rerank failed — %s", _LOG_LABEL, exc)
            return hits[:top_k]
        return apply_rerank_indices(hits, results, top_k=top_k)


def build_default_provider(
    cfg: GitHubIndexConfig | None = None,
) -> GitHubRagProvider | None:
    if not env_enabled("V2_GITHUB_RAG_ENABLED"):
        return None
    try:
        from open_pulse_sources.index.github_repos.config import load_config  # noqa: PLC0415

        resolved = cfg or load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to load config (%s)", _LOG_LABEL, exc)
        return None
    try:
        return GitHubRagProvider.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", _LOG_LABEL, exc)
        return None


__all__ = ["GitHubRagProvider", "build_default_provider"]
