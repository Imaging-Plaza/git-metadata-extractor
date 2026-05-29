-- Zenodo communities index schema. Idempotent.

CREATE TABLE IF NOT EXISTS communities (
    -- Canonical IRI: the dereferenceable landing-page URL for the
    -- community at its source (`https://zenodo.org/communities/<slug>`).
    -- Built by
    -- `src.index.zenodo_communities.iri.canonical_community_id(source, slug)`.
    community_id    TEXT PRIMARY KEY,
    source          TEXT NOT NULL,        -- 'zenodo' (later: 'github', 'openalex', ...)
    source_slug     TEXT NOT NULL,        -- raw slug at the source
    parent_org      TEXT,                 -- 'epfl' | 'ethz' | 'cern' | 'cern_openlab'
    title           TEXT,
    description     TEXT,                 -- HTML-stripped
    url             TEXT,                 -- canonical landing page
    visibility      TEXT,                 -- 'public' | 'restricted' | ...
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP,
    curator_names   JSON,                 -- list of curator display names
    member_count    INTEGER,
    record_count    INTEGER,
    keywords        JSON,                 -- list of free-text keywords / topics
    raw             JSON,
    ingested_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comm_parent_org  ON communities (parent_org);
CREATE INDEX IF NOT EXISTS idx_comm_source      ON communities (source);
CREATE INDEX IF NOT EXISTS idx_comm_slug        ON communities (source_slug);

-- One-shot migration: legacy rows use `zenodo:<slug>` as the primary key;
-- new ingests write the full IRI. Re-keying is idempotent — the WHERE
-- clause matches zero rows on re-runs. `community_id` is a PRIMARY KEY,
-- but UPDATEs that don't change cardinality work fine in DuckDB.
UPDATE communities
   SET community_id = 'https://zenodo.org/communities/' || source_slug
 WHERE source = 'zenodo' AND community_id LIKE 'zenodo:%';
