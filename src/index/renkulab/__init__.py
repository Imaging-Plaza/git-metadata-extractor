"""RenkuLab RAG indexer.

Pulls public projects, groups, users, and data connectors from the
RenkuLab data API (https://renkulab.io/api/data) into DuckDB, then
chunks + embeds the human-readable surfaces (name, description,
keywords, namespace) via RCP and pushes vectors into Qdrant.

Pipeline shape (mirrors src/index/zenodo):

    ingest  → REST → DuckDB. Five entity tables (projects, groups,
              users, data_connectors) plus two link tables
              (project_members, group_members).
    embed   → chunk + embed per-entity_type, push to Qdrant under
              the `renkulab_<entity_type>` collections (or a single
              merged `renkulab` collection — see embed/pipeline.py).
    search  → vector + RCP rerank + DuckDB hydrate.
    query   → predefined or guarded ad-hoc SQL.

The Renku `/users` and `/namespaces` endpoints require auth, but the
public `/search/query?q=type:User` endpoint is open and exposes
sufficient surface (path, slug, first/last name) for an index, so
we harvest users via search.
"""
