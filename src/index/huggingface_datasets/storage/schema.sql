-- DuckDB schema for the huggingface_datasets index.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS datasets (
    repo_id              TEXT PRIMARY KEY,
    author               TEXT,
    sha                  TEXT,
    license              TEXT,
    downloads            BIGINT,
    downloads_all_time   BIGINT,
    likes                BIGINT,
    gated                BOOLEAN,
    private              BOOLEAN,
    created_at           TIMESTAMP,
    last_modified        TIMESTAMP,
    tags                 JSON,
    card_data            JSON,
    dataset_info         JSON,
    -- HF dataset payloads carry a BibTeX `citation` field and an
    -- optional `paperswithcode_id` linking to paperswithcode.com.
    -- We keep the raw BibTeX text and pull any DOIs out into a
    -- separate JSON list of `https://doi.org/...` URLs.
    citation_text        TEXT,
    paperswithcode_url   TEXT,
    citation_dois        JSON,
    raw                  JSON,
    ingested_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                        -- "datasets"
    entity_id    TEXT NOT NULL,                        -- repo_id
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_datasets_author    ON datasets (author);
CREATE INDEX IF NOT EXISTS idx_datasets_license   ON datasets (license);
CREATE INDEX IF NOT EXISTS idx_datasets_downloads ON datasets (downloads);
CREATE INDEX IF NOT EXISTS idx_chunks_entity      ON chunks (entity_type, entity_id);
