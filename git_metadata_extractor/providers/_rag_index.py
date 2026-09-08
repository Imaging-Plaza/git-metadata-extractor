"""One implementation of the single-collection RAG provider, plus its data table.

Thirteen `providers/*_rag.py` modules were 78-96% textually identical: the same
`search` → embed → Qdrant → optional-rerank → thin-payload flow, differing only
in a collection name, a filter allowlist, a thin-key projection, an env var and
a config module. This module holds that flow once; each provider module keeps
only its `RagIndexSpec` and a two-line subclass.

**What is deliberately *not* unified.** The textual similarity overstates how
interchangeable these are, and `tests/v2/test_rag_provider_contract.py` pins
each difference:

- **Scope parameter names are API.** `ror`/`snsf` take `scope_mode`,
  `openalex`/`huggingface` take `collection`, `orcid`/`oamonitor` take
  `entity_type` — and agent tools pass them by keyword. Unifying them on one
  name would type-check, pass the existing tool tests, and break every caller
  at runtime, so a scoped provider keeps its own `search` signature and
  delegates the body here.
- **Two collaborator shapes.** Eleven providers take
  `(store, embedder, reranker)`; `ror` and `snsf` take `(store, rcp)`. Only the
  former can use `from_config` below as-is.
- **Two gating layers.** Eleven providers gate on their env var inside
  `build_default_provider`; `infoscience` and `ethz_research_collection` are
  gated by `dependencies.py` instead. `build_provider` here implements the
  former; it must not be applied to those two without also removing their
  entry from `dependencies.py`.

Because the RAG path needs Qdrant — absent in dev and CI — a refactor here
yields an empty corpus diff whether it is right or wrong. The contract tests
are the safety net, and they are mutation-checked.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from open_pulse_sources.index._rcp.embed_client import (
    RCPEmbeddingClient,
    RCPEmbeddingError,
)
from open_pulse_sources.index._rcp.reranker_client import RCPRerankerClient
from open_pulse_sources.index.openalex.vector.qdrant_store import QdrantStore

from git_metadata_extractor.providers._rag_helpers import (
    apply_rerank_indices,
    env_enabled,
    expand_candidate_k,
    filter_allowlist,
    safe_rerank_documents,
    thin_payload,
    to_simple_filter_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 10


def _default_rerank_text(payload: dict[str, Any]) -> str:
    """Fallback document for the cross-encoder: whatever names the record."""
    name = (
        payload.get("title")
        or payload.get("display_name")
        or payload.get("name")
        or ""
    )
    return str(name).strip()


@dataclass(frozen=True, slots=True)
class RagIndexSpec:
    """Everything that differs between two single-collection RAG providers."""

    #: Log prefix, and the name operators grep for. Historically the module
    #: name (`github_rag`), so it is kept explicit rather than derived.
    label: str
    collection: str
    #: Dotted path to the index's `load_config`, imported lazily so a missing
    #: sibling index cannot break service startup.
    config_module: str
    allowed_filter_keys: frozenset[str]
    thin_keys: tuple[str, ...]
    #: `None` means "gated elsewhere" — see the module docstring.
    env_var: str | None = None
    default_top_k: int = DEFAULT_TOP_K
    rerank_text: Callable[[dict[str, Any]], str] = field(
        default=_default_rerank_text,
    )


class SingleCollectionRagProvider:
    """Async wrapper around one Qdrant collection.

    Every failure path returns `[]` rather than raising: a RAG index is an
    optional enrichment, and a down Qdrant must degrade extraction instead of
    failing the request.
    """

    SPEC: ClassVar[RagIndexSpec]

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
    def from_config(cls, cfg: Any) -> SingleCollectionRagProvider:
        # Duck-typed into OpenAlex's clients: every index config carries the
        # same `rcp.*` / `qdrant.*` shape, which is why one constructor works
        # across all of them.
        return cls(
            store=QdrantStore(cfg),
            embedder=RCPEmbeddingClient(cfg),
            reranker=RCPRerankerClient(cfg),
        )

    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._search(
            query,
            collection=self.SPEC.collection,
            top_k=top_k,
            filters=filters,
            rerank=rerank,
        )

    async def _search(  # noqa: PLR0913 — one keyword per axis of per-call variability
        self,
        query: str,
        *,
        collection: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
        thin_keys: tuple[str, ...] | None = None,
        rerank_text: Callable[[dict[str, Any]], str] | None = None,
    ) -> list[dict[str, Any]]:
        """The shared flow. Scoped providers pass their resolved collection.

        `thin_keys` and `rerank_text` are overridable because a scoped index
        projects different fields per scope (an OpenAlex *work* and an OpenAlex
        *institution* share no columns).
        """
        spec = self.SPEC
        label = spec.label
        resolved_top_k = spec.default_top_k if top_k is None else top_k

        if not isinstance(query, str) or not query.strip():
            return []
        if not self._store.client.collection_exists(collection):
            logger.info(
                "%s: collection %s missing — returning [] without indexing",
                label,
                collection,
            )
            return []

        cleaned = filter_allowlist(
            filters,
            spec.allowed_filter_keys,
            log_label=label,
        )
        filter_payload = to_simple_filter_payload(cleaned)

        try:
            vectors = await self._embedder.embed_all([query])
        except (RCPEmbeddingError, Exception) as exc:  # noqa: BLE001
            logger.warning("%s: embed failed — %s", label, exc)
            return []
        if not vectors:
            return []

        candidate_k = expand_candidate_k(resolved_top_k) if rerank else resolved_top_k

        try:
            hits = await asyncio.to_thread(
                self._store.search,
                collection,
                query_vector=list(vectors[0]),
                top_k=candidate_k,
                filter_payload=filter_payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: qdrant search failed — %s", label, exc)
            return []

        if rerank and hits and self._reranker is not None:
            hits = await self._rerank(
                query,
                hits,
                top_k=resolved_top_k,
                rerank_text=rerank_text or spec.rerank_text,
            )
        else:
            hits = hits[:resolved_top_k]

        keys = thin_keys or spec.thin_keys
        return [
            thin_payload(
                hit.get("payload") or {},
                keys,
                extras={
                    "id": hit.get("id"),
                    "score": hit.get("score"),
                    "collection": collection,
                },
            )
            for hit in hits
        ]

    async def _rerank(
        self,
        query: str,
        hits: list[dict[str, Any]],
        *,
        top_k: int,
        rerank_text: Callable[[dict[str, Any]], str],
    ) -> list[dict[str, Any]]:
        documents = safe_rerank_documents(
            [rerank_text(hit.get("payload") or {}) for hit in hits],
        )
        try:
            results = await self._reranker.rerank(query, documents, top_n=top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: rerank failed — %s", self.SPEC.label, exc)
            return hits[:top_k]
        return apply_rerank_indices(hits, results, top_k=top_k)


def build_provider(
    provider_cls: type[SingleCollectionRagProvider],
    cfg: Any = None,
) -> SingleCollectionRagProvider | None:
    """Construct `provider_cls`, or `None` if anything is unavailable.

    Returns `None` for a disabled index, an unloadable config and a failed
    construction alike. The caller cannot act differently on those, and the
    guarantee that matters is that none of them raises.
    """
    spec = provider_cls.SPEC
    if spec.env_var is not None and not env_enabled(spec.env_var):
        return None
    try:
        module = importlib.import_module(spec.config_module)
        resolved = cfg or module.load_config()
    except Exception as exc:  # noqa: BLE001 — config errors must not 500 the API
        logger.warning("%s: failed to load config (%s)", spec.label, exc)
        return None
    try:
        return provider_cls.from_config(resolved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed to construct provider (%s)", spec.label, exc)
        return None


__all__ = [
    "DEFAULT_TOP_K",
    "RagIndexSpec",
    "SingleCollectionRagProvider",
    "build_provider",
]
