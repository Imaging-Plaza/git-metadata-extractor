-- Canonical DuckDB schema for the SWISSUbase index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.
--
-- Entity model (mirrors SWISSUbase's own ontology):
--
--   studies        the catalogue's top-level item (UI label: "Project").
--   datasets       0..N children of a study (UI label: "Resource").
--   persons        authors / principal investigators / former collaborators.
--   institutions   organisations the study is affiliated with.
--
-- Per the project requirement, every entity preserves its canonical
-- SWISSUbase URL in `source_url`. For studies and datasets this is
-- NOT NULL; persons and institutions don't always have detail pages.

CREATE TABLE IF NOT EXISTS studies (
    study_id              TEXT PRIMARY KEY,            -- numeric ref, stored as TEXT
    ref                   TEXT,
    title                 TEXT,
    description           TEXT,                         -- HTML-stripped abstract
    description_language  TEXT,
    start_date            DATE,
    end_date              DATE,
    progress              TEXT,                         -- e.g. "Finished", "In progress"
    main_discipline       TEXT,
    sub_discipline        TEXT,
    version               TEXT,
    data_availability     TEXT,
    dataset_count         INTEGER,
    affiliation_match     BOOLEAN NOT NULL DEFAULT FALSE,
    source_url            TEXT NOT NULL,
    raw_overview          JSON,
    raw_dynamic_blocks    JSON,
    ingested_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id    TEXT PRIMARY KEY,
    study_id      TEXT NOT NULL,
    title         TEXT,
    description   TEXT,
    access_right  TEXT,
    license_id    TEXT,
    file_count    INTEGER,
    source_url    TEXT NOT NULL,
    raw           JSON,
    ingested_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS persons (
    person_key     TEXT PRIMARY KEY,                   -- ORCID URL when known, else "name:slugified"
    display_name   TEXT,
    orcid          TEXT,
    affiliation    TEXT,
    source_url     TEXT,
    raw            JSON,
    ingested_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS institutions (
    institution_key  TEXT PRIMARY KEY,                 -- ROR URL when known, else "name:slugified"
    name             TEXT,
    address          TEXT,
    ror_id           TEXT,
    source_url       TEXT,
    raw              JSON,
    ingested_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS study_persons (
    study_id     TEXT NOT NULL,
    person_key   TEXT NOT NULL,
    role         TEXT,                                  -- e.g. "Principal investigator", "Former collaborator"
    position     INTEGER,
    PRIMARY KEY (study_id, person_key, role)
);

CREATE TABLE IF NOT EXISTS study_institutions (
    study_id          TEXT NOT NULL,
    institution_key   TEXT NOT NULL,
    PRIMARY KEY (study_id, institution_key)
);

-- chunk_id is deterministic: uuid5(NAMESPACE_URL, "<entity_type>|<entity_id>|<index>")
-- so the primary key alone provides the (entity_type, entity_id, chunk_index)
-- uniqueness guarantee.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                         -- studies | datasets | persons | institutions
    entity_id    TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_studies_progress         ON studies (progress);
CREATE INDEX IF NOT EXISTS idx_studies_main_discipline  ON studies (main_discipline);
CREATE INDEX IF NOT EXISTS idx_studies_affiliation      ON studies (affiliation_match);
CREATE INDEX IF NOT EXISTS idx_datasets_study           ON datasets (study_id);
CREATE INDEX IF NOT EXISTS idx_datasets_access          ON datasets (access_right);
CREATE INDEX IF NOT EXISTS idx_persons_orcid            ON persons (orcid);
CREATE INDEX IF NOT EXISTS idx_institutions_ror         ON institutions (ror_id);
CREATE INDEX IF NOT EXISTS idx_study_persons_pkey       ON study_persons (person_key);
CREATE INDEX IF NOT EXISTS idx_study_institutions_ikey  ON study_institutions (institution_key);
CREATE INDEX IF NOT EXISTS idx_chunks_entity            ON chunks (entity_type, entity_id);
