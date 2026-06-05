-- Derived facet tables for the SNSF P3 index.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.
-- Built by build_facets() in src/index/snsf/facets.py after any ingest.

-- Flattened person ↔ grant ↔ role mapping.
-- Keyed on the canonical grant URL (grant_number).
CREATE TABLE IF NOT EXISTS grant_persons (
    grant_number   TEXT NOT NULL,
    person_number  INTEGER NOT NULL,
    role           TEXT NOT NULL,
    PRIMARY KEY (grant_number, person_number, role)
);

-- Per-grant output rollups (one row per grant in the grants table).
CREATE TABLE IF NOT EXISTS grant_output_counts (
    grant_number              TEXT PRIMARY KEY,
    n_publications            INTEGER NOT NULL DEFAULT 0,
    n_datasets                INTEGER NOT NULL DEFAULT 0,
    n_collaborations          INTEGER NOT NULL DEFAULT 0,
    n_academic_events         INTEGER NOT NULL DEFAULT 0,
    n_knowledge_transfers     INTEGER NOT NULL DEFAULT 0,
    n_public_communications   INTEGER NOT NULL DEFAULT 0,
    n_use_inspired            INTEGER NOT NULL DEFAULT 0
);

-- Distinct collaboration countries per grant.
CREATE TABLE IF NOT EXISTS grant_countries (
    grant_number  TEXT NOT NULL,
    country       TEXT NOT NULL,
    PRIMARY KEY (grant_number, country)
);

CREATE INDEX IF NOT EXISTS grant_persons_person_idx ON grant_persons(person_number);
CREATE INDEX IF NOT EXISTS grant_persons_role_idx   ON grant_persons(role);
CREATE INDEX IF NOT EXISTS grant_countries_country_idx ON grant_countries(country);
