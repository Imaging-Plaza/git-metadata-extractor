-- DuckDB schema for the huggingface_models index.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS models (
    repo_id              TEXT PRIMARY KEY,             -- canonical URL https://huggingface.co/<repo_id>
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
    tags                 JSON,                         -- list of strings
    card_data            JSON,                         -- card.metadata block
    base_models          JSON,                         -- list of repo_ids
    -- arXiv DOIs derived from `arxiv:<id>` tags. arXiv mints a DOI for
    -- every preprint as `10.48550/arXiv.<id>`; we store the canonical
    -- `https://doi.org/...` form so consumers can dereference directly.
    arxiv_dois           JSON,
    raw                  JSON,                         -- full HF model_info payload
    ingested_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- chunks contract identical to the github_* / huggingface_papers
-- indices — `entity_type='models'` + `entity_id=models.repo_id`.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                        -- "models"
    entity_id    TEXT NOT NULL,                        -- repo_id
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_models_author       ON models (author);
CREATE INDEX IF NOT EXISTS idx_models_pipeline_tag ON models (pipeline_tag);
CREATE INDEX IF NOT EXISTS idx_models_library      ON models (library_name);
CREATE INDEX IF NOT EXISTS idx_models_downloads    ON models (downloads);
CREATE INDEX IF NOT EXISTS idx_chunks_entity       ON chunks (entity_type, entity_id);
