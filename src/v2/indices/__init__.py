"""Per-index ingest routes mounted under `/v2/indices/<name>/...` on the v2 API.

Each module here exposes the helpers wired into `src/v2/api.py`: a request
validator, a background runner that calls into the index's existing ingest
helpers, and any lazy-init plumbing for the underlying DuckDB store. Job
records are kept in :class:`IndexIngestJobStore`, namespaced separately from
the extract job store so the two never collide.
"""
