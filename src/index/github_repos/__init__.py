"""GitHub repository RAG indexer.

Pipeline shape (mirrors `src/index/zenodo`):

    ingest         → fetches GitHub repo metadata + README via REST (deterministic,
                     rule-based; no LLM), upserts into DuckDB and writes the
                     README to `cards/{owner}/{name}/README.md`.
    embed          → chunks full_name + description + topics + README, embeds
                     via RCP, upserts into Qdrant. Re-runs skip repos whose
                     chunks are already present.
    rebuild-qdrant → re-derives Qdrant points from the existing `chunks` table
                     (used after a Qdrant wipe; does not touch DuckDB).
    search         → vector + RCP rerank + DuckDB hydrate.
    query          → predefined or guarded ad-hoc SQL over DuckDB.

Phase 1 default scope: `epfl` (curated YAML seed). Phase 2 extends to
`switzerland` by extending the YAML seed (no code changes needed); the
OpenAlex `work_github_urls` table can bootstrap new candidates.
"""
