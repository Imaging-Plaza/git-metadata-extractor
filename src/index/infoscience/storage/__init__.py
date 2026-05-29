"""Storage layer for the infoscience index — DuckDB tables for articles,
persons, organisations, their bipartite edges, extracted artefact links,
and the chunks bookkeeping shared with the Qdrant vector store.

Mirrors the sister-index pattern in `src/index/openalex/storage/` and
`src/index/huggingface/storage/`.
"""

from .duckdb_store import InfoscienceStore

__all__ = ["InfoscienceStore"]
