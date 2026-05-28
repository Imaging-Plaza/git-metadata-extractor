"""Open Access Monitor CH (OAM-CH) RAG indexer.

Wraps the public OAM Mongo-proxy API at ``https://oam.oamonitor.ch/api/data/public``
into a DuckDB-backed local store with per-entity Qdrant collections so the v2
``/v2/indices/oamonitor/{ingest,search}`` routes can serve the same shape as
the other indices.

Four entity tables — ``journals``, ``publications``, ``publishers``,
``organisations`` — mirror the upstream Mongo collections. Each row keeps the
upstream ``_id`` as primary key, plus a curated set of denormalised columns
for filtering and a JSON ``raw`` blob for round-tripping the rest. The
``embed`` and ``retrieve`` stages reuse the OpenAlex RCP embedder, reranker
and Qdrant store via duck typing on the shared ``rcp.*`` / ``qdrant.*``
config sub-blocks.
"""
