-- Canonical DuckDB schema for the ROR index module (D16).
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.
-- See .internal/ror/duckdb-migration.md for column-by-column rationale.

CREATE TABLE IF NOT EXISTS records (
    ror_id              TEXT PRIMARY KEY,
    ror_id_short        TEXT UNIQUE,
    name                TEXT,
    search_blob         TEXT,
    status              TEXT,
    country_code        TEXT,
    country_name        TEXT,
    city                TEXT,
    region              TEXT,
    established         INTEGER,
    website             TEXT,
    types_json          JSON,
    domains_json        JSON,
    names_json          JSON,
    aliases_json        JSON,
    acronyms_json       JSON,
    external_ids_json   JSON,
    relationships_json  JSON,
    record              JSON,
    ror_release_version TEXT,
    ingested_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS records_country_idx ON records(country_code);
CREATE INDEX IF NOT EXISTS records_status_idx  ON records(status);

CREATE TABLE IF NOT EXISTS scope_records (
    scope_mode  TEXT NOT NULL,
    ror_id      TEXT NOT NULL,
    text        TEXT,
    vector_id   TEXT,
    embedded_at TIMESTAMP,
    PRIMARY KEY (scope_mode, ror_id)
);

CREATE INDEX IF NOT EXISTS scope_records_vector_idx ON scope_records(vector_id);
CREATE INDEX IF NOT EXISTS scope_records_ror_idx    ON scope_records(ror_id);

CREATE TABLE IF NOT EXISTS manifests (
    scope_mode          TEXT PRIMARY KEY,
    record_count        INTEGER,
    embedding_model     TEXT,
    embedding_dim       INTEGER,
    reranker_model      TEXT,
    ror_release_version TEXT,
    ror_release_doi     TEXT,
    built_at_iso        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
