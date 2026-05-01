"""ROR (Research Organization Registry) local index + RAG.

Pipeline:

    download (Zenodo dump) ──► filter ──► document ──► embed ──► store (FAISS + JSONL)
                          │
                          └────► dump_index (lazy in-memory inverted index, full dump)

    query_rag(text)  → FAISS retrieval + Qwen3-Reranker-8B
    lookup_dump(...) → exact ROR-ID / lexical lookup over the full dump
    query(text, mode="auto")

Entry point: `python -m src.index.ror <subcommand>`. See `.internal/ror/`.
"""
