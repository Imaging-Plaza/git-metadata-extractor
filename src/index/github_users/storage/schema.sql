-- DuckDB schema for the github_users index.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS users (
    login              TEXT PRIMARY KEY,             -- canonical URL https://github.com/<login>
    github_id          BIGINT,                       -- stable across renames
    node_id            TEXT,
    name               TEXT,
    bio                TEXT,
    company            TEXT,
    blog               TEXT,
    location           TEXT,
    email              TEXT,
    twitter_username   TEXT,
    hireable           BOOLEAN,
    public_repos       BIGINT,
    public_gists       BIGINT,
    followers          BIGINT,
    following          BIGINT,
    account_type       TEXT,                         -- "User" (always for this table)
    avatar_url         TEXT,
    html_url           TEXT,
    created_at         TIMESTAMP,
    updated_at         TIMESTAMP,
    raw                JSON,                         -- full /users/{login} payload
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- chunks contract identical to the repo index — `entity_type='users'`
-- and `entity_id=users.login` for joining with vectors in Qdrant.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                      -- "users"
    entity_id    TEXT NOT NULL,                      -- login
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_github_id ON users (github_id);
CREATE INDEX IF NOT EXISTS idx_users_company   ON users (company);
CREATE INDEX IF NOT EXISTS idx_users_location  ON users (location);
CREATE INDEX IF NOT EXISTS idx_chunks_entity   ON chunks (entity_type, entity_id);
