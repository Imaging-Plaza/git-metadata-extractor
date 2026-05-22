-- Canonical DuckDB schema for the infoscience index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.
-- Mirrors the openalex / huggingface sister-index pattern.

CREATE TABLE IF NOT EXISTS articles (
    article_uuid       TEXT PRIMARY KEY,
    title              TEXT,
    abstract           TEXT,
    doi                TEXT,
    publication_year   INTEGER,
    publication_type   TEXT,
    journal            TEXT,
    language           TEXT,
    infoscience_url    TEXT,
    raw                JSON,
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS persons (
    person_uuid        TEXT PRIMARY KEY,
    display_name       TEXT,
    given_name         TEXT,
    family_name        TEXT,
    orcid              TEXT,
    sciper_id          TEXT,
    primary_affiliation       TEXT,
    primary_affiliation_uuid  TEXT,
    raw                JSON,
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS organizations (
    org_uuid           TEXT PRIMARY KEY,
    name               TEXT,
    acronym            TEXT,
    -- Alternative EPFL-internal codes that complement `acronym`.
    -- `infoscience_code` is the U-prefixed form (`U13780`); some EPFL
    -- systems reference units by this. `unit_code` is the bare numeric
    -- (`13780`) for joins with HR / EPFL Graph data.
    infoscience_code   TEXT,
    unit_code          TEXT,
    -- Canonical Infoscience entity URL —
    -- `https://infoscience.epfl.ch/entities/orgunit/<uuid>`.
    infoscience_url    TEXT,
    parent_org_uuid    TEXT,
    -- Parent acronym mirrored from `organization.parentOrganization`
    -- (`BMI`, `SV`, ...). Useful when querying "all units under X"
    -- without a UUID join.
    parent_acronym     TEXT,
    -- Director / unit-manager name (`crisou.director`).
    director_name      TEXT,
    -- DSpace entity sub-type (`LABO`, `FACULTY`, `SECTION`, ...).
    org_type_dspace    TEXT,
    sciper_unit_id     TEXT,
    ror_id             TEXT,
    raw                JSON,
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Bipartite article ↔ person, ordered by author position.
CREATE TABLE IF NOT EXISTS article_persons (
    article_uuid   TEXT NOT NULL,
    person_uuid    TEXT NOT NULL,
    position       INTEGER,
    PRIMARY KEY (article_uuid, person_uuid)
);

-- Bipartite article ↔ organisation. `field` records which DSpace field
-- the authority came from (department / parent-organization / affiliation).
CREATE TABLE IF NOT EXISTS article_orgs (
    article_uuid   TEXT NOT NULL,
    org_uuid       TEXT NOT NULL,
    field          TEXT NOT NULL,
    PRIMARY KEY (article_uuid, org_uuid, field)
);

-- Every artefact-host URL extracted from an article body, plus the Solr
-- phrase that matched it. Populated by the link sweep
-- (scripts/dump_link_articles.py) and refreshed on subsequent runs.
CREATE TABLE IF NOT EXISTS article_links (
    article_uuid    TEXT NOT NULL,
    host_label      TEXT NOT NULL,   -- 'github' | 'arxiv' | 'orcid' | ...
    url             TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('phrase_match', 'body_text')),
    PRIMARY KEY (article_uuid, host_label, url, source)
);

-- chunk_id is deterministic: uuid5(NAMESPACE_URL, "<entity_type>|<entity_id>|<index>")
-- so the primary key alone provides the (entity_type, entity_id, chunk_index)
-- uniqueness guarantee. See `embed/pipeline.py:_chunk_id` for the canonical helper
-- (or build/store.py if it lives there for infoscience).
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

CREATE INDEX IF NOT EXISTS idx_articles_year      ON articles (publication_year);
CREATE INDEX IF NOT EXISTS idx_articles_doi       ON articles (doi);
CREATE INDEX IF NOT EXISTS idx_persons_orcid      ON persons (orcid);
CREATE INDEX IF NOT EXISTS idx_persons_sciper     ON persons (sciper_id);
CREATE INDEX IF NOT EXISTS idx_orgs_ror           ON organizations (ror_id);
CREATE INDEX IF NOT EXISTS idx_orgs_parent        ON organizations (parent_org_uuid);
CREATE INDEX IF NOT EXISTS idx_article_links_host ON article_links (host_label);
CREATE INDEX IF NOT EXISTS idx_article_links_url  ON article_links (url);
CREATE INDEX IF NOT EXISTS idx_chunks_entity      ON chunks (entity_type, entity_id);
