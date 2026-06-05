-- Canonical DuckDB schema for the SNSF P3 index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.
-- See .internal/snsf/README.md for column-by-column rationale.

-- One row per SNSF P3 grant (Application). Loaded from the bulk CSV
-- `grants_with_abstracts.csv`. 90,448 grants total, 1975-2027.
CREATE TABLE IF NOT EXISTS grants (
    -- v3.0.0: the id is the canonical SNSF grant URL
    -- (`https://data.snf.ch/grants/grant/<n>`), not the bare integer.
    -- `SnsfStore.bootstrap()` migrates legacy INTEGER rows in place.
    grant_number             TEXT PRIMARY KEY,
    grant_number_string      TEXT,
    title                    TEXT,
    title_english            TEXT,
    responsible_applicant    TEXT,

    funding_instrument       TEXT,
    funding_instrument_reporting TEXT,
    funding_instrument_l1    TEXT,

    institute                TEXT,
    institute_city           TEXT,
    institute_country        TEXT,
    research_institution     TEXT,
    research_institution_type TEXT,

    main_discipline          TEXT,
    main_discipline_number   TEXT,
    main_discipline_l1       TEXT,
    main_discipline_l2       TEXT,
    all_disciplines          TEXT,
    main_field_of_research   TEXT,
    main_field_of_research_la TEXT,
    main_field_of_research_lb TEXT,
    all_field_of_researchs   TEXT,

    start_date               TIMESTAMP,
    end_date                 TIMESTAMP,
    amount_granted           BIGINT,
    keywords                 TEXT,

    -- Full project abstract (technical, English usually). Multi-line free text.
    abstract                 TEXT,

    -- Plain-language summary in 4 languages (English / German / French / Italian).
    -- The "lead" is a single-sentence headline; the full lay summary is a paragraph.
    lay_summary_lead_en      TEXT,
    lay_summary_en           TEXT,
    lay_summary_lead_de      TEXT,
    lay_summary_de           TEXT,
    lay_summary_lead_fr      TEXT,
    lay_summary_fr           TEXT,
    lay_summary_lead_it      TEXT,
    lay_summary_it           TEXT,

    state                    TEXT,
    call_full_title          TEXT,
    call_end_date            TIMESTAMP,
    call_decision_year       INTEGER,

    ingested_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS grants_research_institution_idx ON grants(research_institution);
CREATE INDEX IF NOT EXISTS grants_state_idx                ON grants(state);
CREATE INDEX IF NOT EXISTS grants_call_year_idx            ON grants(call_decision_year);
CREATE INDEX IF NOT EXISTS grants_main_discipline_idx      ON grants(main_discipline_number);

-- One row per SNSF-known person. Loaded from `persons.csv`.
-- Each person has lists of grants they're attached to via different roles
-- (`responsible_applicant`, `co_applicant`, `employee`, etc.) — these are
-- semicolon-separated TEXT in the source CSV; we keep them as JSON arrays
-- so callers can `json_each` to expand the join.
CREATE TABLE IF NOT EXISTS persons (
    person_number                       INTEGER PRIMARY KEY,
    first_name                          TEXT,
    last_name                           TEXT,
    institute                           TEXT,
    institute_place                     TEXT,
    institute_country                   TEXT,
    research_institution                TEXT,
    research_institution_type           TEXT,
    orcid                               TEXT,
    -- Per-role grant_number lists, JSON-encoded for `json_each` ergonomics.
    responsible_applicant_grants        JSON,
    co_applicant_grants                 JSON,
    project_partner_grants              JSON,
    practice_partner_grants             JSON,
    employee_grants                     JSON,
    contact_person_grants               JSON,
    applicant_abroad_grants             JSON,
    person_grant_discipline             TEXT,
    person_grant_keywords               TEXT,
    ingested_at                         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS persons_orcid_idx                ON persons(orcid);
CREATE INDEX IF NOT EXISTS persons_research_institution_idx ON persons(research_institution);

-- Reference taxonomy from `SNF_field_of_research_disciplines.csv`. ~360 rows.
-- Maps SNSF disciplines (`mySNF discipline #`) to ARC's Field of Research codes.
CREATE TABLE IF NOT EXISTS discipline_taxonomy (
    mapping_direction        TEXT,
    field_of_research_number INTEGER,
    field_of_research        TEXT,
    snf_discipline_number    INTEGER,
    snf_discipline           TEXT
);

-- Per-scope grant membership. (scope_mode, grant_number).
CREATE TABLE IF NOT EXISTS scope_records (
    scope_mode   TEXT NOT NULL,
    grant_number TEXT NOT NULL,
    PRIMARY KEY (scope_mode, grant_number)
);

CREATE INDEX IF NOT EXISTS scope_records_grant_idx ON scope_records(grant_number);

-- Per-scope ingest manifest. Tracks the most recent dump version + load timestamp.
CREATE TABLE IF NOT EXISTS manifests (
    scope_mode    TEXT PRIMARY KEY,
    record_count  INTEGER,
    snapshot_iso  TIMESTAMP,
    source_dir    TEXT,
    built_at_iso  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Output tables
--
-- All loaded from `output_data_*.csv`. Every row is keyed by `grant_number`
-- (FK to `grants.grant_number`). To slice by scope, JOIN to `scope_records`.
-- The PK on each is the source CSV's UUID column.
-- ---------------------------------------------------------------------------

-- output_data_scientific_publications.csv — 23 cols including DOI + Abstract.
CREATE TABLE IF NOT EXISTS output_publications (
    publication_id           TEXT PRIMARY KEY,
    grant_number             TEXT,
    peer_review_status       TEXT,
    type                     TEXT,
    title                    TEXT,
    author                   TEXT,
    state                    TEXT,
    year                     INTEGER,
    isbn                     TEXT,
    doi                      TEXT,
    import_source            TEXT,
    open_access_yes_no       INTEGER,    -- 0/1 in source
    open_access_status       TEXT,
    url                      TEXT,
    publication_title        TEXT,
    publisher                TEXT,
    editor                   TEXT,
    volume                   TEXT,
    issue_number             TEXT,
    first_page_number        TEXT,
    last_page_number         TEXT,
    proceeding_location      TEXT,
    abstract                 TEXT,
    ingested_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS output_pubs_grant_idx ON output_publications(grant_number);
CREATE INDEX IF NOT EXISTS output_pubs_doi_idx   ON output_publications(doi);
CREATE INDEX IF NOT EXISTS output_pubs_year_idx  ON output_publications(year);

-- output_data_academicevents.csv — talks, posters, conferences.
CREATE TABLE IF NOT EXISTS output_academic_events (
    event_id                 TEXT PRIMARY KEY,
    grant_number             TEXT,
    type                     TEXT,
    event                    TEXT,
    contribution_title       TEXT,
    date                     TIMESTAMP,
    involved_person          TEXT,
    url                      TEXT,
    place                    TEXT,
    ingested_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS output_events_grant_idx ON output_academic_events(grant_number);
CREATE INDEX IF NOT EXISTS output_events_date_idx  ON output_academic_events(date);

-- output_data_collaborations.csv — partner research groups + country.
CREATE TABLE IF NOT EXISTS output_collaborations (
    collaboration_id         TEXT PRIMARY KEY,
    grant_number             TEXT,
    research_group           TEXT,
    type                     TEXT,
    country                  TEXT,
    start_date               TIMESTAMP,
    end_date                 TIMESTAMP,
    ingested_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS output_collab_grant_idx   ON output_collaborations(grant_number);
CREATE INDEX IF NOT EXISTS output_collab_country_idx ON output_collaborations(country);

-- output_data_datasets.csv — research data outputs (with PID like Zenodo DOI).
CREATE TABLE IF NOT EXISTS output_datasets (
    dataset_id               TEXT PRIMARY KEY,
    grant_number             TEXT,
    title                    TEXT,
    author                   TEXT,
    persistent_identifier    TEXT,                -- DOI / Handle / etc.
    repository_name          TEXT,
    repository_link          TEXT,
    publication_date         TIMESTAMP,
    abstract                 TEXT,
    ingested_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS output_ds_grant_idx ON output_datasets(grant_number);
CREATE INDEX IF NOT EXISTS output_ds_pid_idx   ON output_datasets(persistent_identifier);

-- output_data_knowledgetransfer.csv — tech transfer events.
CREATE TABLE IF NOT EXISTS output_knowledge_transfers (
    event_id                 TEXT PRIMARY KEY,
    grant_number             TEXT,
    type                     TEXT,
    event                    TEXT,
    date                     TIMESTAMP,
    involved_person          TEXT,
    url                      TEXT,
    place                    TEXT,
    target_group             TEXT,
    ingested_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS output_kt_grant_idx ON output_knowledge_transfers(grant_number);

-- output_data_publiccommunications.csv — outreach + lay press.
CREATE TABLE IF NOT EXISTS output_public_communications (
    communication_id         TEXT PRIMARY KEY,
    grant_number             TEXT,
    type                     TEXT,
    title                    TEXT,
    description              TEXT,
    year                     INTEGER,
    url                      TEXT,
    region                   TEXT,
    ingested_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS output_pc_grant_idx ON output_public_communications(grant_number);

-- output_data_useinspired.csv — patents, startups, licenses.
CREATE TABLE IF NOT EXISTS output_use_inspired (
    use_inspired_id          TEXT PRIMARY KEY,
    grant_number             TEXT,
    type                     TEXT,
    title                    TEXT,
    url                      TEXT,
    year                     INTEGER,
    priority_date            TIMESTAMP,
    patent_number            TEXT,
    patent_status            TEXT,
    patent_decision_date     TIMESTAMP,
    inventor                 TEXT,
    owner                    TEXT,
    patent_owner_description TEXT,
    comment                  TEXT,
    reviewer_activity_type   TEXT,
    license_type             TEXT,
    ingested_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS output_ui_grant_idx ON output_use_inspired(grant_number);

-- ---------------------------------------------------------------------------
-- Derived facet tables (built by src/index/snsf/facets.py :: build_facets)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS grant_persons (
    grant_number   TEXT NOT NULL,
    person_number  INTEGER NOT NULL,
    role           TEXT NOT NULL,
    PRIMARY KEY (grant_number, person_number, role)
);

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

CREATE TABLE IF NOT EXISTS grant_countries (
    grant_number  TEXT NOT NULL,
    country       TEXT NOT NULL,
    PRIMARY KEY (grant_number, country)
);

CREATE INDEX IF NOT EXISTS grant_persons_person_idx    ON grant_persons(person_number);
CREATE INDEX IF NOT EXISTS grant_persons_role_idx      ON grant_persons(role);
CREATE INDEX IF NOT EXISTS grant_countries_country_idx ON grant_countries(country);
