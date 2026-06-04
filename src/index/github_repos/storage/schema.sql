-- Canonical DuckDB schema for the GitHub index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS repos (
    repo_id            TEXT PRIMARY KEY,             -- canonical URL https://github.com/<owner>/<name>
    owner              TEXT NOT NULL,
    name               TEXT NOT NULL,
    default_branch     TEXT,
    description        TEXT,
    homepage           TEXT,
    primary_language   TEXT,
    languages          JSON,                         -- {lang: bytes}
    topics             JSON,                         -- list of strings
    license_spdx       TEXT,
    is_fork            BOOLEAN,
    is_archived        BOOLEAN,
    is_private         BOOLEAN,
    stargazers_count   BIGINT,
    forks_count        BIGINT,
    watchers_count     BIGINT,
    open_issues_count  BIGINT,
    size_kb            BIGINT,
    created_at         TIMESTAMP,
    pushed_at          TIMESTAMP,
    readme_path        TEXT,                         -- relative to <INDEX_DATA_DIR>/github/cards
    contributors       JSON,                         -- [{login, contributions}, ...]
    raw                JSON,                         -- full REST repos/{owner}/{name} payload
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- chunk_id is deterministic: uuid5(NAMESPACE_URL, "<entity_type>|<entity_id>|<index>")
-- so the primary key alone provides uniqueness.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                      -- "repos" for now
    entity_id    TEXT NOT NULL,                      -- repo_id
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_repos_owner    ON repos (owner);
CREATE INDEX IF NOT EXISTS idx_repos_lang     ON repos (primary_language);
CREATE INDEX IF NOT EXISTS idx_repos_pushed   ON repos (pushed_at);
CREATE INDEX IF NOT EXISTS idx_repos_archived ON repos (is_archived);
CREATE INDEX IF NOT EXISTS idx_chunks_entity  ON chunks (entity_type, entity_id);
