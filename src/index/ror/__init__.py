"""ROR (Research Organization Registry) local index + RAG.

Pipeline (D16):

    download (Zenodo dump) ──► filter ──► document ──► embed ──► Qdrant + DuckDB
                                                              │
                                                              ▼
                                              records (full dump) +
                                              scope_records (per-scope membership) +
                                              manifests (per-scope build metadata)

    query_rag(text)  → Qdrant retrieval + Qwen3-Reranker-8B
    lookup_dump(...) → SQL over the DuckDB `records` table (no RCP calls)
    query(text, mode="auto")

Entry point: `python -m src.index.ror <subcommand>`. See `.internal/ror/`.
"""
