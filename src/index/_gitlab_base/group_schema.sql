CREATE TABLE IF NOT EXISTS groups (
    group_id          TEXT PRIMARY KEY,   -- canonical web_url (https://<host>/groups/<full_path>)
    host              TEXT NOT NULL,      -- e.g. gitlab.epfl.ch
    full_path         TEXT NOT NULL,      -- group path or path_with_namespace for subgroups
    name              TEXT,
    description       TEXT,
    visibility        TEXT,
    parent            TEXT,               -- parent group web_url or NULL
    web_url           TEXT,
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
CREATE INDEX IF NOT EXISTS idx_gl_groups_host ON groups (host);
CREATE INDEX IF NOT EXISTS idx_gl_chunks_entity ON chunks (entity_type, entity_id);
