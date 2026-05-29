-- Canonical DuckDB schema for the Zenodo index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS records (
    zenodo_id          TEXT PRIMARY KEY,            -- canonical version-record ID (post-redirect)
    concept_recid      TEXT,                         -- Zenodo concept record (groups all versions)
    -- DOIs. `doi` is the version-level DOI; `concept_doi` (Zenodo's
    -- `conceptdoi`) resolves to "all versions" and is the citation
    -- consumers usually want long-term. Both are nullable — older
    -- records pre-DOI minting carry neither.
    doi                TEXT,
    concept_doi        TEXT,
    title              TEXT,
    description        TEXT,                         -- HTML-stripped
    publication_date   DATE,
    resource_type      TEXT,                         -- e.g. publication-article, dataset, software
    access_right       TEXT,                         -- open | embargoed | restricted | closed
    license_id         TEXT,
    -- Free-text `metadata.version` ("1.2.3", "v1", …). Distinct from
    -- `revision` which is Zenodo's internal monotonically-increasing
    -- counter incremented on any metadata edit.
    version            TEXT,
    revision           INTEGER,
    -- Lifecycle timestamps from the Zenodo API. `created_at` is the
    -- record's first publication; `updated_at` flips on every revision
    -- (metadata edit, file replacement). Both come from the top-level
    -- `created` / `updated` fields on the API payload, NOT from
    -- `metadata.publication_date` (which is the human-set publication
    -- date and can lag/lead the record itself).
    created_at         TIMESTAMP,
    updated_at         TIMESTAMP,
    -- Aggregated reach metrics from the Zenodo `stats` sub-block.
    -- `*_views` and `*_downloads` are total counts; the `unique_`
    -- variants dedupe by visitor / downloader IP+UA. The non-prefixed
    -- columns are the concept-level totals (sum across every version
    -- of the record); `version_*` is just this specific version.
    views              BIGINT,
    unique_views       BIGINT,
    downloads          BIGINT,
    unique_downloads   BIGINT,
    version_views      BIGINT,
    version_unique_views BIGINT,
    version_downloads  BIGINT,
    version_unique_downloads BIGINT,
    keywords_json      JSON,
    -- Denormalised list of community slugs the record belongs to.
    -- Mirrors `record_communities` so consumers can filter
    -- `WHERE list_contains(community_ids, 'cernopenlab')` without
    -- joining. Refreshed at upsert time.
    community_ids      JSON,
    -- The first community we crawled when ingesting this record;
    -- handy as a `primary` label when a record lives in multiple
    -- communities and we just need a single colour-code field.
    primary_community_id TEXT,
    raw                JSON,
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS creators (
    creator_key   TEXT PRIMARY KEY,                  -- ORCID URL when available, else slugified normalized name
    display_name  TEXT,
    orcid         TEXT,
    affiliation   TEXT,
    raw           JSON,
    ingested_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS record_creators (
    record_id     TEXT NOT NULL,
    creator_key   TEXT NOT NULL,
    position      INTEGER,
    PRIMARY KEY (record_id, creator_key)
);

CREATE TABLE IF NOT EXISTS communities (
    community_id  TEXT PRIMARY KEY,                  -- slug, e.g. "epfl"
    title         TEXT,
    raw           JSON,
    ingested_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS record_communities (
    record_id     TEXT NOT NULL,
    community_id  TEXT NOT NULL,
    PRIMARY KEY (record_id, community_id)
);

CREATE TABLE IF NOT EXISTS files (
    record_id    TEXT NOT NULL,
    file_key     TEXT NOT NULL,                      -- filename
    file_id      TEXT,
    size_bytes   BIGINT,
    checksum     TEXT,
    download_url TEXT,
    PRIMARY KEY (record_id, file_key)
);

-- chunk_id is deterministic: uuid5(NAMESPACE_URL, "<entity_type>|<entity_id>|<index>")
-- so the primary key alone provides the (entity_type, entity_id, chunk_index)
-- uniqueness guarantee.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                      -- "records" for now
    entity_id    TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_records_pubdate     ON records (publication_date);
CREATE INDEX IF NOT EXISTS idx_records_type        ON records (resource_type);
CREATE INDEX IF NOT EXISTS idx_records_access      ON records (access_right);
CREATE INDEX IF NOT EXISTS idx_records_concept     ON records (concept_recid);
CREATE INDEX IF NOT EXISTS idx_records_primary_comm ON records (primary_community_id);
CREATE INDEX IF NOT EXISTS idx_records_updated     ON records (updated_at);
CREATE INDEX IF NOT EXISTS idx_records_views       ON records (views);
CREATE INDEX IF NOT EXISTS idx_records_downloads   ON records (downloads);
CREATE INDEX IF NOT EXISTS idx_creators_orcid      ON creators (orcid);
CREATE INDEX IF NOT EXISTS idx_chunks_entity       ON chunks (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_record_creators_ck  ON record_creators (creator_key);
CREATE INDEX IF NOT EXISTS idx_record_comm_cid     ON record_communities (community_id);

-- One-shot IRI migration. Pre-existing rows used bare numeric ids
-- (records.zenodo_id) and bare slugs (communities.community_id); new
-- ingest writes IRI form. The UPDATEs below converge every legacy row
-- to the canonical IRI; each clause is idempotent because the WHERE
-- filter ('LIKE') matches zero rows on re-runs.

-- records.zenodo_id: '18314844' → 'https://zenodo.org/records/18314844'
UPDATE records
   SET zenodo_id = 'https://zenodo.org/records/' || zenodo_id
 WHERE zenodo_id NOT LIKE 'https://%';

-- records.primary_community_id: 'epfl' → 'https://zenodo.org/communities/epfl'
UPDATE records
   SET primary_community_id = 'https://zenodo.org/communities/' || primary_community_id
 WHERE primary_community_id IS NOT NULL
   AND primary_community_id NOT LIKE 'https://%';

-- records.community_ids JSON list — promote each element. DuckDB's
-- json_transform can't easily rewrite list elements, so we rebuild
-- the JSON array client-side via list_transform.
UPDATE records
   SET community_ids = (
       SELECT to_json(list_transform(
           CAST(community_ids AS VARCHAR[]),
           x -> CASE WHEN x LIKE 'https://%'
                     THEN x
                     ELSE 'https://zenodo.org/communities/' || x
                END
       ))
   )
 WHERE community_ids IS NOT NULL
   AND CAST(community_ids AS VARCHAR) LIKE '%"%"%'  -- non-empty list
   AND CAST(community_ids AS VARCHAR) NOT LIKE '%"https://%';  -- has bare slugs

-- communities.community_id: 'epfl' → 'https://zenodo.org/communities/epfl'
UPDATE communities
   SET community_id = 'https://zenodo.org/communities/' || community_id
 WHERE community_id NOT LIKE 'https://%';

-- NOTE: Link table FK rewrites (record_creators, record_communities,
-- files, chunks.entity_id) are NOT handled here — DuckDB's index
-- maintenance chokes on the bulk UPDATE for large tables
-- (~24k record_creators rows hits "Failed to delete all rows from
-- index"). Those migrations live in `duckdb_store.py::bootstrap()`,
-- which uses a CTAS-swap idiom that sidesteps the bug.
