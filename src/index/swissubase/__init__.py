"""SWISSUbase RAG indexer.

SWISSUbase is the Swiss national platform for sharing and preserving
research data, run by FORS / UZH / U. Neuchâtel / DASCH. The catalogue
exposes ~13k "studies" (which the user-facing UI calls "projects") plus
their child datasets, principal investigators, and partner institutions.

Pipeline shape (mirrors src/index/zenodo):

    ingest  → drive a Selenium browser session through the catalogue, scrape
              the Material table, then call the per-study public REST
              endpoints (which require a session cookie) inside the same
              browser to fetch overview + dynamic blocks. Persist into
              DuckDB.
    embed   → chunk title + abstract + keywords for in-scope studies (and
              their datasets / persons / institutions), embed via RCP, push
              to Qdrant.
    search  → vector + RCP rerank + DuckDB hydrate.
    query   → predefined or guarded ad-hoc SQL.

Default scope: ``epfl_sdsc_ethz`` — the search has no institution filter
server-side, so we ingest the full catalogue but only embed studies whose
institution string matches EPFL / ETH Zurich / SDSC. Flip
``INDEX_SWISSUBASE_SCOPE=switzerland`` to embed everything.
"""
