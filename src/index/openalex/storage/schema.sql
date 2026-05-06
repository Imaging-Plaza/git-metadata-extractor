-- Canonical DuckDB schema for the OpenAlex index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.
-- See .internal/openalex/SCHEMA.md for column-by-column rationale.

CREATE TABLE IF NOT EXISTS works (
    openalex_id        TEXT PRIMARY KEY,
    doi                TEXT,
    title              TEXT,
    abstract           TEXT,
    publication_year   INTEGER,
    primary_topic_id   TEXT,
    primary_source_id  TEXT,
    raw                JSON,
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS authors (
    openalex_id                 TEXT PRIMARY KEY,
    display_name                TEXT,
    orcid                       TEXT,
    last_known_institution_id   TEXT,
    raw                         JSON,
    ingested_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS institutions (
    openalex_id    TEXT PRIMARY KEY,
    ror            TEXT,
    display_name   TEXT,
    country_code   TEXT,
    raw            JSON,
    ingested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
    openalex_id    TEXT PRIMARY KEY,
    issn_l         TEXT,
    display_name   TEXT,
    type           TEXT,
    raw            JSON,
    ingested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topics (
    openalex_id    TEXT PRIMARY KEY,
    display_name   TEXT,
    domain_id      TEXT,
    field_id       TEXT,
    raw            JSON,
    ingested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS concepts (
    openalex_id    TEXT PRIMARY KEY,
    display_name   TEXT,
    level          INTEGER,
    raw            JSON,
    ingested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_authors (
    work_id     TEXT NOT NULL,
    author_id   TEXT NOT NULL,
    position    INTEGER,
    PRIMARY KEY (work_id, author_id)
);

CREATE TABLE IF NOT EXISTS work_institutions (
    work_id          TEXT NOT NULL,
    institution_id   TEXT NOT NULL,
    PRIMARY KEY (work_id, institution_id)
);

CREATE TABLE IF NOT EXISTS work_github_urls (
    work_id          TEXT NOT NULL,
    url              TEXT NOT NULL,
    normalized_url   TEXT NOT NULL,
    owner            TEXT,
    repo             TEXT,
    source           TEXT NOT NULL CHECK (source IN ('abstract', 'fulltext')),
    found_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (work_id, normalized_url)
);

-- chunk_id is deterministic: uuid5(NAMESPACE_URL, "<entity_type>|<entity_id>|<index>")
-- so the primary key alone provides the (entity_type, entity_id, chunk_index)
-- uniqueness guarantee. See `embed/pipeline.py:_chunk_id`.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    token_count     INTEGER NOT NULL,
    vector_id       TEXT NOT NULL,
    embedded_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_works_year         ON works (publication_year);
CREATE INDEX IF NOT EXISTS idx_works_topic        ON works (primary_topic_id);
CREATE INDEX IF NOT EXISTS idx_authors_orcid      ON authors (orcid);
CREATE INDEX IF NOT EXISTS idx_institutions_ror   ON institutions (ror);
CREATE INDEX IF NOT EXISTS idx_institutions_cc    ON institutions (country_code);
CREATE INDEX IF NOT EXISTS idx_chunks_entity      ON chunks (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_github_urls_norm   ON work_github_urls (normalized_url);
CREATE INDEX IF NOT EXISTS idx_github_urls_source ON work_github_urls (source);
