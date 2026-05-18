-- OAM-CH index — DuckDB schema.
-- Idempotent. Bootstrapped from `OamonitorStore.bootstrap()`.

CREATE TABLE IF NOT EXISTS journals (
    _id              VARCHAR PRIMARY KEY,
    title            VARCHAR,
    oa_color         INTEGER,
    issns            VARCHAR[],
    updated          TIMESTAMP,
    embedding_text   VARCHAR,
    raw              JSON,
    fetched_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS publications (
    _id              VARCHAR PRIMARY KEY,
    doi              VARCHAR,
    url              VARCHAR,
    oa_color         INTEGER,
    license          VARCHAR,
    published_year   INTEGER,
    publisher_id     VARCHAR,
    publisher_name   VARCHAR,
    source_id        VARCHAR,
    source_title     VARCHAR,
    organisation_ids VARCHAR[],
    updated          TIMESTAMP,
    embedding_text   VARCHAR,
    raw              JSON,
    fetched_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS publishers (
    _id              VARCHAR PRIMARY KEY,
    name             VARCHAR,
    oa_color         INTEGER,
    updated          TIMESTAMP,
    embedding_text   VARCHAR,
    raw              JSON,
    fetched_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS organisations (
    _id              VARCHAR PRIMARY KEY,
    name             VARCHAR,
    type             VARCHAR,
    grid_id          VARCHAR,
    country_code     VARCHAR,
    acronyms         VARCHAR[],
    aliases          VARCHAR[],
    updated          TIMESTAMP,
    embedding_text   VARCHAR,
    raw              JSON,
    fetched_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Per-row embed bookkeeping: NULL means "never embedded"; otherwise the
-- timestamp at which the row's embedding_text was pushed into Qdrant.
-- The embed pipeline filters on `embedded_at IS NULL OR embedded_at < updated`
-- so refreshed upstream rows get re-embedded on the next pass.
ALTER TABLE journals       ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMP;
ALTER TABLE publications   ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMP;
ALTER TABLE publishers     ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMP;
ALTER TABLE organisations  ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_publications_doi ON publications(doi);
CREATE INDEX IF NOT EXISTS idx_publications_year ON publications(published_year);
CREATE INDEX IF NOT EXISTS idx_publications_publisher ON publications(publisher_id);
CREATE INDEX IF NOT EXISTS idx_publications_source ON publications(source_id);
CREATE INDEX IF NOT EXISTS idx_organisations_country ON organisations(country_code);
CREATE INDEX IF NOT EXISTS idx_journals_embedded      ON journals(embedded_at);
CREATE INDEX IF NOT EXISTS idx_publications_embedded  ON publications(embedded_at);
CREATE INDEX IF NOT EXISTS idx_publishers_embedded    ON publishers(embedded_at);
CREATE INDEX IF NOT EXISTS idx_organisations_embedded ON organisations(embedded_at);
