CREATE TABLE IF NOT EXISTS projects (
    project_id        TEXT PRIMARY KEY,   -- canonical web_url
    host              TEXT NOT NULL,      -- e.g. gitlab.epfl.ch
    full_path         TEXT NOT NULL,      -- path_with_namespace
    name              TEXT,
    description       TEXT,
    visibility        TEXT,
    is_fork           BOOLEAN,
    forked_from       TEXT,               -- parent project web_url or NULL
    namespace         TEXT,
    topics            JSON,
    star_count        BIGINT,
    forks_count       BIGINT,
    default_branch    TEXT,
    last_activity_at  TIMESTAMP,
    created_at        TIMESTAMP,
    raw               JSON,
    ingested_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gl_projects_host ON projects (host);
CREATE INDEX IF NOT EXISTS idx_gl_chunks_entity ON chunks (entity_type, entity_id);
