"""Zenodo RAG indexer.

Pipeline shape (mirrors src/index/openalex):

    ingest  → fetches Zenodo records (REST `/api/records` filtered by
              community), upserts into DuckDB.
    embed   → chunks title + description, embeds via RCP, upserts into
              Qdrant. Idempotent re-runs skip records that already have chunks.
    search  → vector + RCP rerank + DuckDB hydrate.
    query   → predefined or guarded ad-hoc SQL over DuckDB.

Phase 1 default scope: `epfl` (curated list of EPFL communities). Phase 2
extends to `switzerland` (Swiss community + ROR fallback).
"""
