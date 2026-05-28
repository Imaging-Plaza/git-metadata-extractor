-- Canonical DuckDB schema for the HuggingFace index module.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.
-- See .internal/huggingface/PLAN.md → "Storage schema" for column rationale.

CREATE TABLE IF NOT EXISTS orgs (
    slug             TEXT PRIMARY KEY,
    namespace_kind   TEXT NOT NULL DEFAULT 'org',  -- 'user' | 'org'
    source           TEXT NOT NULL DEFAULT 'seed', -- 'seed' | 'discover'
    scope            TEXT NOT NULL,                -- 'epfl' | 'switzerland'
    fullname         TEXT,                         -- HF display name
    details          TEXT,                         -- HF org/user bio (often empty)
    avatar_url       TEXT,
    num_models       BIGINT,
    num_datasets     BIGINT,
    num_spaces       BIGINT,
    num_followers    BIGINT,
    raw              JSON,                         -- full HF overview payload
    ingested_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Additive migrations for DBs created before the orgs overview columns existed.
-- DuckDB silently no-ops `ADD COLUMN IF NOT EXISTS` when the column is already there.
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS fullname      TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS details       TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS avatar_url    TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS num_models    BIGINT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS num_datasets  BIGINT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS num_spaces    BIGINT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS num_followers BIGINT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS raw           JSON;

CREATE TABLE IF NOT EXISTS models (
    repo_id              TEXT PRIMARY KEY,
    author               TEXT,
    sha                  TEXT,
    pipeline_tag         TEXT,
    library_name         TEXT,
    license              TEXT,
    downloads            BIGINT,
    downloads_all_time   BIGINT,
    likes                BIGINT,
    gated                BOOLEAN,
    private              BOOLEAN,
    created_at           TIMESTAMP,
    last_modified        TIMESTAMP,
    tags                 JSON,
    card_data            JSON,
    base_models          JSON,
    -- arXiv DOIs derived from `arxiv:<id>` tags. arXiv mints a DOI for
    -- every preprint as `10.48550/arXiv.<id>`; we store the canonical
    -- `https://doi.org/...` form so consumers can dereference directly.
    arxiv_dois           JSON,
    raw                  JSON,
    ingested_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS datasets (
    repo_id              TEXT PRIMARY KEY,
    author               TEXT,
    sha                  TEXT,
    license              TEXT,
    downloads            BIGINT,
    downloads_all_time   BIGINT,
    likes                BIGINT,
    gated                BOOLEAN,
    private              BOOLEAN,
    created_at           TIMESTAMP,
    last_modified        TIMESTAMP,
    tags                 JSON,
    card_data            JSON,
    dataset_info         JSON,
    -- HF dataset payloads carry a BibTeX `citation` field and an
    -- optional `paperswithcode_id` linking to paperswithcode.com.
    -- We keep the raw BibTeX text and pull any DOIs out into a
    -- separate JSON list of `https://doi.org/...` URLs.
    citation_text        TEXT,
    paperswithcode_url   TEXT,
    citation_dois        JSON,
    raw                  JSON,
    ingested_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Additive migrations for DBs created before the citation-surface
-- columns landed. Idempotent (`IF NOT EXISTS`).
ALTER TABLE models   ADD COLUMN IF NOT EXISTS arxiv_dois         JSON;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS citation_text      TEXT;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS paperswithcode_url TEXT;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS citation_dois      JSON;

CREATE TABLE IF NOT EXISTS spaces (
    repo_id              TEXT PRIMARY KEY,
    author               TEXT,
    sha                  TEXT,
    sdk                  TEXT,
    runtime_stage        TEXT,
    hardware             TEXT,
    license              TEXT,
    likes                BIGINT,
    created_at           TIMESTAMP,
    last_modified        TIMESTAMP,
    tags                 JSON,
    card_data            JSON,
    raw                  JSON,
    ingested_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- chunk_id is deterministic: uuid5(NAMESPACE_URL, "<entity_type>|<repo_id>|<index>")
-- so the primary key alone provides the (entity_type, repo_id, chunk_index)
-- uniqueness guarantee. See `embed/pipeline.py:_chunk_id`.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,        -- 'model' | 'dataset' | 'space'
    repo_id         TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    token_count     INTEGER NOT NULL,
    vector_id       TEXT NOT NULL,
    embedded_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_models_author        ON models (author);
CREATE INDEX IF NOT EXISTS idx_models_pipeline_tag  ON models (pipeline_tag);
CREATE INDEX IF NOT EXISTS idx_models_library_name  ON models (library_name);
CREATE INDEX IF NOT EXISTS idx_models_license       ON models (license);
CREATE INDEX IF NOT EXISTS idx_datasets_author      ON datasets (author);
CREATE INDEX IF NOT EXISTS idx_datasets_license     ON datasets (license);
CREATE INDEX IF NOT EXISTS idx_spaces_author        ON spaces (author);
CREATE INDEX IF NOT EXISTS idx_spaces_sdk           ON spaces (sdk);
CREATE INDEX IF NOT EXISTS idx_orgs_scope           ON orgs (scope);
CREATE INDEX IF NOT EXISTS idx_chunks_entity        ON chunks (entity_type, repo_id);
