-- DuckDB schema for the github_organizations index.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS organizations (
    login                       TEXT PRIMARY KEY,         -- canonical URL https://github.com/<login>
    github_id                   BIGINT,                   -- stable across renames
    node_id                     TEXT,
    name                        TEXT,
    description                 TEXT,
    blog                        TEXT,
    location                    TEXT,
    email                       TEXT,
    twitter_username            TEXT,
    company                     TEXT,
    public_repos                BIGINT,
    public_gists                BIGINT,
    followers                   BIGINT,
    following                   BIGINT,
    is_verified                 BOOLEAN,
    has_organization_projects   BOOLEAN,
    has_repository_projects     BOOLEAN,
    account_type                TEXT,                     -- "Organization"
    avatar_url                  TEXT,
    html_url                    TEXT,
    created_at                  TIMESTAMP,
    updated_at                  TIMESTAMP,
    raw                         JSON,                     -- full /orgs/{org} payload
    ingested_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                           -- "organizations"
    entity_id    TEXT NOT NULL,                           -- login
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_orgs_github_id ON organizations (github_id);
CREATE INDEX IF NOT EXISTS idx_orgs_location  ON organizations (location);
CREATE INDEX IF NOT EXISTS idx_chunks_entity  ON chunks (entity_type, entity_id);
