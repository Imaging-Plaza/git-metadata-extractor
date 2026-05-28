-- EPFL Graph disciplines index — DuckDB schema.
-- Idempotent. Bootstrapped from `EpflGraphStore.bootstrap()`.

CREATE TABLE IF NOT EXISTS categories (
    category_id          VARCHAR PRIMARY KEY,
    name                 VARCHAR,
    depth                INTEGER,
    parent_id            VARCHAR,
    wikipedia_page_id    VARCHAR,
    wikipedia_url        VARCHAR,
    wikipedia_extract    VARCHAR,
    wikidata_qid         VARCHAR,
    graphsearch_url      VARCHAR,
    n_concepts           INTEGER DEFAULT 0,
    n_children           INTEGER DEFAULT 0,
    embedding_text       VARCHAR,
    raw                  JSON,
    fetched_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Idempotent column adds for upgrades from older schemas.
ALTER TABLE categories ADD COLUMN IF NOT EXISTS wikipedia_extract VARCHAR;
ALTER TABLE categories ADD COLUMN IF NOT EXISTS wikidata_qid VARCHAR;

CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_categories_depth ON categories(depth);

CREATE TABLE IF NOT EXISTS category_concepts (
    category_id   VARCHAR,
    concept_id    VARCHAR,
    concept_name  VARCHAR,
    rank          INTEGER,
    PRIMARY KEY (category_id, concept_id)
);

CREATE INDEX IF NOT EXISTS idx_category_concepts_cat
    ON category_concepts(category_id);
