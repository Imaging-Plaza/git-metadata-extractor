-- Canonical DuckDB schema for the Zenodo index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS records (
    zenodo_id          TEXT PRIMARY KEY,            -- numeric, stored as TEXT for parity with openalex_id
    doi                TEXT,
    title              TEXT,
    description        TEXT,                         -- HTML-stripped
    publication_date   DATE,
    resource_type      TEXT,                         -- e.g. publication-article, dataset, software
    access_right       TEXT,                         -- open | embargoed | restricted | closed
    license_id         TEXT,
    keywords_json      JSON,
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
CREATE INDEX IF NOT EXISTS idx_creators_orcid      ON creators (orcid);
CREATE INDEX IF NOT EXISTS idx_chunks_entity       ON chunks (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_record_creators_ck  ON record_creators (creator_key);
CREATE INDEX IF NOT EXISTS idx_record_comm_cid     ON record_communities (community_id);
