"""Async RAG provider over the EPFL Graph disciplines Qdrant index.

Single collection: ``epfl_graph_disciplines`` — the curated academic-discipline
ontology (~2226 categories, depth 1..5). The search flow lives in
`_rag_index.SingleCollectionRagProvider`; this module is the index's data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from open_pulse_sources.index.epfl_graph.embed.pipeline import EPFL_GRAPH_COLLECTION

from git_metadata_extractor.providers._rag_index import (
    RagIndexSpec,
    SingleCollectionRagProvider,
    build_provider,
)

if TYPE_CHECKING:
    from open_pulse_sources.index.epfl_graph.config import EpflGraphIndexConfig


def _rerank_text(payload: dict[str, Any]) -> str:
    name = payload.get("name") or payload.get("category_id") or ""
    text = payload.get("embedding_text") or ""
    return f"{name}\n{text}".strip()


SPEC = RagIndexSpec(
    label="epfl_graph_rag",
    collection=EPFL_GRAPH_COLLECTION,
    config_module="open_pulse_sources.index.epfl_graph.config",
    env_var="V2_EPFL_GRAPH_RAG_ENABLED",
    allowed_filter_keys=frozenset(
        {"category_id", "depth", "parent_id", "entity_type"},
    ),
    thin_keys=(
        "category_id",
        "name",
        "depth",
        "parent_id",
        "wikipedia_page_id",
        "wikipedia_url",
        "graphsearch_url",
        "n_concepts",
        "n_children",
    ),
    rerank_text=_rerank_text,
)


class EpflGraphRagProvider(SingleCollectionRagProvider):
    """Async wrapper around the EPFL Graph disciplines Qdrant index."""

    SPEC: ClassVar[RagIndexSpec] = SPEC


def build_default_provider(
    cfg: EpflGraphIndexConfig | None = None,
) -> EpflGraphRagProvider | None:
    return build_provider(EpflGraphRagProvider, cfg)  # type: ignore[return-value]


__all__ = ["EpflGraphRagProvider", "build_default_provider"]
