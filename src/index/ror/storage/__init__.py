"""DuckDB-backed metadata store for the ROR index.

See `.internal/ror/duckdb-migration.md` (D16) for the design rationale.
"""

from src.index.ror.storage.duckdb_store import (
    RorStore,
    ScopeRecord,
    build_search_blob,
    extract_record_columns,
    fold_for_search,
)

__all__ = [
    "RorStore",
    "ScopeRecord",
    "build_search_blob",
    "extract_record_columns",
    "fold_for_search",
]
