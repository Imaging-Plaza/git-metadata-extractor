-- Canonical DuckDB schema for the RenkuLab index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS projects (
    project_id        TEXT PRIMARY KEY,         -- ULID, e.g. "01KN1WYG..."
    slug              TEXT,
    name              TEXT,
    namespace         TEXT,                      -- "user.name" or "group/project"
    path              TEXT,                      -- full namespace + slug path
    description       TEXT,
    visibility        TEXT,                      -- public | private | internal
    is_template       BOOLEAN,
    keywords_json     JSON,
    repositories_json JSON,                      -- list of git URLs
    created_by        TEXT,                      -- user UUID
    creation_date     TIMESTAMP,
    updated_at        TIMESTAMP,
    raw               JSON,
    ingested_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS groups (
    group_id      TEXT PRIMARY KEY,             -- ULID
    slug          TEXT,
    name          TEXT,
    description   TEXT,
    created_by    TEXT,
    creation_date TIMESTAMP,
    raw           JSON,
    ingested_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,             -- UUID
    slug          TEXT,
    path          TEXT,                          -- usually equals slug
    first_name    TEXT,
    last_name     TEXT,
    raw           JSON,
    ingested_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_connectors (
    data_connector_id TEXT PRIMARY KEY,         -- ULID
    slug              TEXT,
    name              TEXT,
    namespace         TEXT,                      -- owning user/group/project path
    path              TEXT,                      -- full path
    description       TEXT,
    visibility        TEXT,
    storage_type      TEXT,                      -- s3 | switchDrive | polybox | doi | ...
    storage_provider  TEXT,                      -- e.g. envidat_v1, shared
    source_path       TEXT,
    target_path       TEXT,
    readonly          BOOLEAN,
    keywords_json     JSON,
    created_by        TEXT,                      -- user UUID
    creation_date     TIMESTAMP,
    raw               JSON,
    ingested_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    role       TEXT,                              -- owner | editor | viewer
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    role       TEXT,
    PRIMARY KEY (project_id, user_id)
);

-- Free-floating chunks table; entity_type discriminates which table
-- entity_id refers to (projects, groups, users, data_connectors).
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

CREATE INDEX IF NOT EXISTS idx_projects_namespace        ON projects (namespace);
CREATE INDEX IF NOT EXISTS idx_projects_visibility       ON projects (visibility);
CREATE INDEX IF NOT EXISTS idx_projects_creation_date    ON projects (creation_date);
CREATE INDEX IF NOT EXISTS idx_groups_slug               ON groups (slug);
CREATE INDEX IF NOT EXISTS idx_users_slug                ON users (slug);
CREATE INDEX IF NOT EXISTS idx_dc_namespace              ON data_connectors (namespace);
CREATE INDEX IF NOT EXISTS idx_dc_storage_type           ON data_connectors (storage_type);
CREATE INDEX IF NOT EXISTS idx_chunks_entity             ON chunks (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_group_members_user        ON group_members (user_id);
CREATE INDEX IF NOT EXISTS idx_project_members_user      ON project_members (user_id);
