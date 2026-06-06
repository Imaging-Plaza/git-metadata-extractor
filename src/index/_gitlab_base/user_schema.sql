CREATE TABLE IF NOT EXISTS users (
    user_id           TEXT PRIMARY KEY,   -- canonical web_url (https://<host>/<username>)
    host              TEXT NOT NULL,      -- e.g. gitlab.epfl.ch
    username          TEXT NOT NULL,
    name              TEXT,
    bio               TEXT,
    location          TEXT,
    organization      TEXT,
    job_title         TEXT,
    public_email      TEXT,
    website_url       TEXT,
    linkedin          TEXT,
    twitter           TEXT,
    avatar_url        TEXT,
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
CREATE INDEX IF NOT EXISTS idx_gl_users_host ON users (host);
CREATE INDEX IF NOT EXISTS idx_gl_chunks_entity ON chunks (entity_type, entity_id);
