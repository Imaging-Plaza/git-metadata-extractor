-- Communities index schema. Idempotent.

CREATE TABLE IF NOT EXISTS communities (
    -- Namespaced primary key: '<source>:<source_slug>' (e.g. 'zenodo:epfl-chili').
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
