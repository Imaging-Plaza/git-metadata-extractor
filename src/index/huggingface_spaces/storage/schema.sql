-- DuckDB schema for the huggingface_spaces index.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS spaces (
    repo_id              TEXT PRIMARY KEY,
    author               TEXT,
    sha                  TEXT,
    sdk                  TEXT,                        -- gradio | streamlit | docker | static
    runtime_stage        TEXT,                        -- RUNNING | SLEEPING | PAUSED | ...
    hardware             TEXT,                        -- cpu-basic | t4-small | ...
    license              TEXT,
    likes                BIGINT,
    created_at           TIMESTAMP,
    last_modified        TIMESTAMP,
    tags                 JSON,
    card_data            JSON,
    raw                  JSON,
    ingested_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                        -- "spaces"
    entity_id    TEXT NOT NULL,                        -- repo_id
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_spaces_author     ON spaces (author);
CREATE INDEX IF NOT EXISTS idx_spaces_sdk        ON spaces (sdk);
CREATE INDEX IF NOT EXISTS idx_spaces_likes      ON spaces (likes);
CREATE INDEX IF NOT EXISTS idx_chunks_entity     ON chunks (entity_type, entity_id);
