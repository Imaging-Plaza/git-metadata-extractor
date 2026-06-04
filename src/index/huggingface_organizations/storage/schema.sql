-- DuckDB schema for the huggingface_organizations index.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS organizations (
    slug             TEXT PRIMARY KEY,             -- canonical URL https://huggingface.co/<slug>
    fullname         TEXT,
    details          TEXT,
    avatar_url       TEXT,
    num_models       BIGINT,
    num_datasets     BIGINT,
    num_spaces       BIGINT,
    num_followers    BIGINT,
    raw              JSON,
    ingested_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                    -- "organizations"
    entity_id    TEXT NOT NULL,                    -- slug
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_orgs_num_followers ON organizations (num_followers);
CREATE INDEX IF NOT EXISTS idx_orgs_num_models    ON organizations (num_models);
CREATE INDEX IF NOT EXISTS idx_chunks_entity      ON chunks (entity_type, entity_id);
