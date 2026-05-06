"""SNSF P3 (Swiss National Science Foundation) local index.

Pipeline (Phase 1):

    POST /api/grants/export ──► paginate ──► DuckDB `grants` table
    POST /api/grants/search ──► (optional --enrich) ──► same row, _source columns

    snsf.lookup(...)         → SQL over `grants` (count, filter, group-by)

Phase 2 will add embedding (Qwen3-Embedding-8B via EPFL RCP) and Qdrant
`snsf_<scope>` collections to mirror the openalex/ror sibling shape.

Entry point: `python -m src.index.snsf <subcommand>`. See `.internal/snsf/`.
"""
