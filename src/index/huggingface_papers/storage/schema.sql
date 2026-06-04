-- DuckDB schema for the huggingface_papers index.
-- Idempotent: every statement uses IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS papers (
    arxiv_id                  TEXT PRIMARY KEY,         -- canonical URL https://huggingface.co/papers/<arxiv_id>
    title                     TEXT NOT NULL,
    summary                   TEXT,                     -- abstract
    doi                       TEXT,                     -- "10.48550/arXiv.<id>"
    authors                   JSON,                     -- [{name, hidden, user_id, affiliation}, ...]
    published_at              TIMESTAMP,
    submitted_at              TIMESTAMP,
    upvotes                   BIGINT,
    num_comments              BIGINT,
    is_author_participating   BOOLEAN,
    ai_summary                TEXT,
    ai_keywords               JSON,                     -- [str, ...]
    thumbnail                 TEXT,
    linked_models             JSON,                     -- [{repo_id, ...}, ...]
    linked_datasets           JSON,                     -- [{repo_id, ...}, ...]
    raw                       JSON,                     -- full /api/papers/<id> payload
    ingested_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- chunks contract identical to the github_users / github_organizations
-- indices — `entity_type='papers'` + `entity_id=papers.arxiv_id`.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,                         -- "papers"
    entity_id    TEXT NOT NULL,                         -- arxiv_id
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_papers_doi          ON papers (doi);
CREATE INDEX IF NOT EXISTS idx_papers_published_at ON papers (published_at);
CREATE INDEX IF NOT EXISTS idx_papers_upvotes      ON papers (upvotes);
CREATE INDEX IF NOT EXISTS idx_chunks_entity       ON chunks (entity_type, entity_id);
