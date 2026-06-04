-- Canonical DuckDB schema for the Docker Hub index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS images (
    repo_id           TEXT PRIMARY KEY,        -- canonical URL https://hub.docker.com/(r/<ns>/<name> | _/<name>)
    namespace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    description       TEXT,                     -- short tagline
    full_description  TEXT,                     -- README markdown
    is_official       BOOLEAN,
    is_automated      BOOLEAN,
    is_private        BOOLEAN,
    star_count        BIGINT,
    pull_count        BIGINT,
    status            TEXT,
    last_updated      TIMESTAMP,
    date_registered   TIMESTAMP,
    tags              JSON,                     -- list of tag strings
    raw               JSON,                     -- full /v2/repositories payload
    ingested_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- chunk_id is deterministic: uuid5(NAMESPACE_URL, "<entity_type>|<entity_id>|<index>")
-- so the primary key alone provides uniqueness.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                 -- "images"
    entity_id    TEXT NOT NULL,                 -- repo_id
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_images_namespace ON images (namespace);
CREATE INDEX IF NOT EXISTS idx_images_official  ON images (is_official);
CREATE INDEX IF NOT EXISTS idx_images_pulls     ON images (pull_count);
CREATE INDEX IF NOT EXISTS idx_chunks_entity    ON chunks (entity_type, entity_id);
