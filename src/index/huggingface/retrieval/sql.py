"""Read-only SQL surface over the DuckDB dump.

- `run_predefined()` — parametrized canned queries. Safe by construction.
- `run_adhoc()` — guarded ad-hoc SELECT/WITH only. Rejects anything that
  doesn't start with SELECT/WITH or that contains forbidden statement-level
  keywords.
"""

from __future__ import annotations

import re
from typing import Any

from src.index.huggingface.storage.duckdb_store import DuckDBStore

INVALID_QUERY_PREFIX_ERROR = "Only SELECT/WITH queries are allowed"
FORBIDDEN_KEYWORD_ERROR = "Forbidden keyword in query: {kw}"

_ALLOWED_PREFIXES = ("select", "with")
_FORBIDDEN_KEYWORDS = (
    "attach",
    "copy",
    "pragma",
    "install",
    "load",
    "export",
    "import",
    "create",
    "drop",
    "alter",
    "insert",
    "update",
    "delete",
    "truncate",
)

_KEYWORD_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)


def _validate_adhoc(sql: str) -> None:
    stripped = sql.strip().lstrip("(").lstrip()
    lowered = stripped.lower()
    if not any(lowered.startswith(p) for p in _ALLOWED_PREFIXES):
        raise ValueError(INVALID_QUERY_PREFIX_ERROR)
    match = _KEYWORD_RE.search(stripped)
    if match:
        raise ValueError(FORBIDDEN_KEYWORD_ERROR.format(kw=match.group(1).upper()))


PREDEFINED_QUERIES: dict[str, str] = {
    "count_by_entity": (
        "SELECT 'orgs' AS entity, COUNT(*) AS n FROM orgs "
        "UNION ALL SELECT 'models', COUNT(*) FROM models "
        "UNION ALL SELECT 'datasets', COUNT(*) FROM datasets "
        "UNION ALL SELECT 'spaces', COUNT(*) FROM spaces "
        "UNION ALL SELECT 'chunks', COUNT(*) FROM chunks"
    ),
    "count_models": "SELECT COUNT(*) AS n FROM models",
    "count_datasets": "SELECT COUNT(*) AS n FROM datasets",
    "count_spaces": "SELECT COUNT(*) AS n FROM spaces",
    "top_models_by_downloads": (
        "SELECT repo_id, author, pipeline_tag, library_name, license, downloads, likes "
        "FROM models ORDER BY downloads DESC NULLS LAST, likes DESC NULLS LAST "
        "LIMIT $limit"
    ),
    "top_datasets_by_downloads": (
        "SELECT repo_id, author, license, downloads, likes "
        "FROM datasets ORDER BY downloads DESC NULLS LAST, likes DESC NULLS LAST "
        "LIMIT $limit"
    ),
    "models_by_author": (
        "SELECT repo_id, pipeline_tag, library_name, license, downloads, likes "
        "FROM models WHERE author = $author "
        "ORDER BY downloads DESC NULLS LAST LIMIT $limit"
    ),
    "datasets_by_author": (
        "SELECT repo_id, license, downloads, likes "
        "FROM datasets WHERE author = $author "
        "ORDER BY downloads DESC NULLS LAST LIMIT $limit"
    ),
    "models_by_pipeline_tag": (
        "SELECT repo_id, author, library_name, license, downloads, likes "
        "FROM models WHERE pipeline_tag = $pipeline_tag "
        "ORDER BY downloads DESC NULLS LAST LIMIT $limit"
    ),
    "orgs_by_scope": (
        "SELECT slug, namespace_kind, source FROM orgs "
        "WHERE scope = $scope ORDER BY slug"
    ),
}


def _row_to_dict(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _execute(
    sql: str,
    params: dict[str, Any] | None,
    store: DuckDBStore | None,
) -> list[dict[str, Any]]:
    owned = False
    if store is None:
        store = DuckDBStore.open()
        owned = True
    try:
        cur = store.connect().execute(sql, params or {})
        return _row_to_dict(cur)
    finally:
        if owned:
            store.close()


def run_adhoc(
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    store: DuckDBStore | None = None,
) -> list[dict[str, Any]]:
    _validate_adhoc(sql)
    return _execute(sql, params, store)


def run_predefined(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    store: DuckDBStore | None = None,
) -> list[dict[str, Any]]:
    if name not in PREDEFINED_QUERIES:
        message = (
            f"Unknown predefined query: {name!r}. "
            f"Known: {sorted(PREDEFINED_QUERIES)}"
        )
        raise ValueError(message)
    return _execute(PREDEFINED_QUERIES[name], params, store)
