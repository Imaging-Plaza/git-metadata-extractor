# Communities Index

A small DuckDB-backed index that enumerates the **Zenodo communities**
belonging to a set of parent organisations (EPFL, ETH Zürich, CERN,
CERN openlab). It is the lookup table that lets the
[Zenodo index](zenodo-index.md) attribute each record to a parent
organisation. Lives at `src/index/communities/`.

## Why it exists

Zenodo records carry community slugs, but a slug like `epfl-chili` or
`cernopenlab` means nothing on its own. The communities index resolves
each slug to a `parent_org`, so:

- `zenodo ingest --scope cern` knows *which* community slugs to crawl
- every persisted Zenodo record can be stamped with `community_ids` and
  `primary_community_id`
- the federated layer can answer "which communities belong to EPFL?"

## Architecture

A single DuckDB file at
`data/index/communities/duckdb/communities.duckdb` with one table,
`communities`. There is no embedding / Qdrant layer — this is a plain
metadata index, queried by exact slug or `parent_org`.

```
config/index/communities.yaml
        │  parents: epfl / ethz / cern / cern_openlab
        ▼
┌─ Build ───────────────────────────────────────────────────────────────┐
│  1. hardcoded_slugs   → GET /api/communities/<slug>   (curator-vetted) │
│  2. discovery_queries → GET /api/communities?q=<kw>   (auto-discovery) │
│        └─ affiliation_check_regex filters fuzzy false-positives        │
│  3. upsert into DuckDB, deduplicated by community_id                   │
└──────┬─────────────────────────────────────────────────────────────────┘
       ▼
   communities (community_id PK, parent_org, source_slug, title, …)
```

## Config

`config/index/communities.yaml` lists one block per parent org:

```yaml
parents:
  cern:
    label: "CERN"
    discovery_queries:
      - "CERN"
      - "ATLAS Collaboration"
    # Applied to title+description of *discovered* hits only — hardcoded
    # slugs bypass it. Zenodo's `?q=` is fuzzy ("CERN" also matches
    # CERMN / CERTH / CERIC), so this regex drops false-positives.
    affiliation_check_regex: "\\b(CERN|LHCb|Large Hadron Collider|cern\\.ch)\\b"
    hardcoded_slugs:
      - cerneducation
      - cernsciencegateway
```

- **`hardcoded_slugs`** — known-good slugs, fetched directly. Always
  kept (curator-vetted).
- **`discovery_queries`** — `?q=<keyword>` searches that pick up
  newly-created communities without editing the file.
- **`affiliation_check_regex`** — optional. Filters discovered hits
  whose title/description does not actually mention the institution.

## CLI

```bash
python -m src.index.communities.cli build              # full build (hardcoded + discovery)
python -m src.index.communities.cli build --no-discovery   # hardcoded slugs only
python -m src.index.communities.cli stats              # row counts per parent_org
```

`build` is idempotent — communities are upserted by `community_id`
(`zenodo:<slug>`), so overlap between hardcoded and discovered slugs is
harmless.

## Schema

```sql
communities (
    community_id       TEXT PRIMARY KEY,   -- e.g. zenodo:epfl-chili
    source             TEXT NOT NULL,      -- "zenodo"
    source_slug        TEXT NOT NULL,      -- the bare Zenodo slug
    parent_org         TEXT,               -- epfl | ethz | cern | cern_openlab
    title              TEXT,
    description        TEXT,               -- HTML-stripped
    url                TEXT,
    visibility         TEXT,
    created_at         TIMESTAMP,
    updated_at         TIMESTAMP,
    curator_names      JSON,
    member_count       INTEGER,
    record_count       INTEGER,
    keywords           JSON,
    raw                JSON,               -- full Zenodo API payload
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

## Federated layer

`src/index/_federated/adapters/communities.py` registers a
DuckDB-backed adapter so the communities index participates in the
cross-index federated fan-out (registered as `communities` in
`src/index/_federated/registry.py`).
