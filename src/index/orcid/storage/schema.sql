-- Canonical DuckDB schema for the ORCID index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.
-- One DuckDB file per scope (see src/index/orcid/paths.py).

CREATE TABLE IF NOT EXISTS persons (
    orcid_id        TEXT PRIMARY KEY,            -- canonical URL https://orcid.org/0000-0000-0000-000X
    given_name      TEXT,
    family_name     TEXT,
    display_name    TEXT,
    biography       TEXT,
    in_scope        BOOLEAN NOT NULL DEFAULT FALSE,
    scope_reason    TEXT,
    discovered_via  TEXT NOT NULL,               -- 'openalex' | 'orcid_search' | 'both' | 'manual'
    raw             JSON,
    ingested_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS employments (
    orcid_id        TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    organization    TEXT NOT NULL,
    org_ror         TEXT,
    department      TEXT,
    role            TEXT,
    start_date      TEXT,
    end_date        TEXT,
    PRIMARY KEY (orcid_id, seq)
);

CREATE TABLE IF NOT EXISTS educations (
    orcid_id        TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    organization    TEXT NOT NULL,
    org_ror         TEXT,
    department      TEXT,
    role            TEXT,
    start_date      TEXT,
    end_date        TEXT,
    PRIMARY KEY (orcid_id, seq)
);

CREATE TABLE IF NOT EXISTS seeds (
    orcid_id        TEXT PRIMARY KEY,
    discovered_via  TEXT NOT NULL,
    hint            TEXT,
    discovered_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- chunk_id is deterministic: uuid5(NAMESPACE_URL, "<entity_type>|<entity_id>|<index>")
-- where entity_id is orcid_id (persons) or "<orcid_id>#<seq>" (employments/educations).
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,               -- 'persons' | 'employments' | 'educations'
    entity_id       TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    token_count     INTEGER NOT NULL,
    vector_id       TEXT NOT NULL,
    embedded_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_persons_inscope    ON persons (in_scope);
CREATE INDEX IF NOT EXISTS idx_persons_family     ON persons (family_name);
CREATE INDEX IF NOT EXISTS idx_emp_ror            ON employments (org_ror);
CREATE INDEX IF NOT EXISTS idx_emp_org            ON employments (organization);
CREATE INDEX IF NOT EXISTS idx_edu_ror            ON educations (org_ror);
CREATE INDEX IF NOT EXISTS idx_chunks_entity      ON chunks (entity_type, entity_id);
