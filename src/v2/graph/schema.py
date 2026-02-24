from __future__ import annotations

VALID_ALIAS_SOURCES = ("ror", "agent", "manual", "derived")

SCHEMA_VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

ENTITIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    identifiers TEXT NOT NULL,
    id_source TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '{}',
    last_seen TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    detected_type TEXT NOT NULL,
    status TEXT NOT NULL,
    stats TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);
"""

EDGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES entities(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES entities(id) ON DELETE CASCADE
);
"""

ALIASES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS aliases (
    id TEXT PRIMARY KEY,
    alias_string TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    canonical_entity_id TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    source TEXT NOT NULL CHECK (source IN ('ror', 'agent', 'manual', 'derived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(alias_normalized),
    FOREIGN KEY (canonical_entity_id) REFERENCES entities(id) ON DELETE CASCADE
);
"""

INTERMEDIATES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS intermediates (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    run_id TEXT,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE SET NULL
);
"""

ENTITIES_TYPE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);"
EDGES_SOURCE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_edges_source_id ON edges(source_id);"
)
EDGES_TARGET_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_edges_target_id ON edges(target_id);"
)
EDGES_RELATION_TYPE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_edges_relation_type ON edges(relation_type);"
)
ALIASES_ENTITY_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_aliases_canonical_entity_id ON aliases(canonical_entity_id);"
)
ALIASES_NORMALIZED_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_aliases_alias_normalized ON aliases(alias_normalized);"
)
INTERMEDIATES_RUN_ID_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_intermediates_run_id ON intermediates(run_id);"
)
RUNS_SOURCE_URL_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_runs_source_url ON runs(source_url);"

INITIAL_SCHEMA_STATEMENTS = (
    SCHEMA_VERSION_TABLE_SQL,
    ENTITIES_TABLE_SQL,
    RUNS_TABLE_SQL,
    EDGES_TABLE_SQL,
    ALIASES_TABLE_SQL,
    INTERMEDIATES_TABLE_SQL,
    ENTITIES_TYPE_INDEX_SQL,
    EDGES_SOURCE_INDEX_SQL,
    EDGES_TARGET_INDEX_SQL,
    EDGES_RELATION_TYPE_INDEX_SQL,
    ALIASES_ENTITY_INDEX_SQL,
    ALIASES_NORMALIZED_INDEX_SQL,
    INTERMEDIATES_RUN_ID_INDEX_SQL,
    RUNS_SOURCE_URL_INDEX_SQL,
)

DROP_ALL_TABLES_STATEMENTS = (
    "DROP TABLE IF EXISTS aliases;",
    "DROP TABLE IF EXISTS edges;",
    "DROP TABLE IF EXISTS intermediates;",
    "DROP TABLE IF EXISTS runs;",
    "DROP TABLE IF EXISTS entities;",
    "DROP TABLE IF EXISTS schema_version;",
)


def build_initial_schema_sql() -> str:
    return "\n\n".join(statement.strip() for statement in INITIAL_SCHEMA_STATEMENTS) + "\n"
