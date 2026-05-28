"""Infoscience EPFL harvest + RAG index.

Pipeline (each stage resumable from disk):

    discover  ──► fetch_text  ──► extract_matches ──► extract_relations
                                                       │
                                                       ▼
                                                   fetch_related
                                                       │
                                       chunk ──► embed ──► store
                                                                │
                                                                ▼
                                              query (filter → vector → rerank)

Entry point: `python -m src.index.infoscience <subcommand>`.
"""
