"""Read-only SQL surface over the DuckDB dump.

Two entrypoints:

- `run_predefined()` — parametrized canned queries. Safe by construction.
- `run_adhoc()` — guarded ad-hoc SELECT/WITH only. Opens DuckDB read-only and
  rejects anything that doesn't start with a SELECT/WITH or that contains
  forbidden statement-level keywords.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from src.index.openalex.storage.duckdb_store import OpenAlexStore

if TYPE_CHECKING:
    pass

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
        "SELECT 'works' AS entity, COUNT(*) AS n FROM works "
        "UNION ALL SELECT 'authors', COUNT(*) FROM authors "
        "UNION ALL SELECT 'institutions', COUNT(*) FROM institutions "
        "UNION ALL SELECT 'sources', COUNT(*) FROM sources "
        "UNION ALL SELECT 'topics', COUNT(*) FROM topics "
        "UNION ALL SELECT 'concepts', COUNT(*) FROM concepts "
        "UNION ALL SELECT 'work_github_urls', COUNT(*) FROM work_github_urls"
    ),
    "top_works_by_year": (
        "SELECT openalex_id, title, publication_year FROM works "
        "WHERE publication_year = $year "
        "ORDER BY title LIMIT $limit"
    ),
    "github_works": (
        "SELECT w.openalex_id, w.title, w.publication_year, gh.normalized_url "
        "FROM works w JOIN work_github_urls gh ON gh.work_id = w.openalex_id "
        "ORDER BY w.publication_year DESC NULLS LAST, w.title "
        "LIMIT $limit"
    ),
    "distinct_github_urls": (
        "SELECT DISTINCT normalized_url FROM work_github_urls "
        "ORDER BY normalized_url LIMIT $limit"
    ),
    "coauthors_of_author": (
        "SELECT a.openalex_id, a.display_name, COUNT(*) AS shared_works "
        "FROM work_authors wa1 JOIN work_authors wa2 "
        "  ON wa1.work_id = wa2.work_id AND wa1.author_id <> wa2.author_id "
        "JOIN authors a ON a.openalex_id = wa2.author_id "
        "WHERE wa1.author_id = $author_id "
        "GROUP BY a.openalex_id, a.display_name "
        "ORDER BY shared_works DESC LIMIT $limit"
    ),
}


def _row_to_dict(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _execute(
    sql: str,
    params: dict[str, Any] | None,
    store: OpenAlexStore | None,
) -> list[dict[str, Any]]:
    # DuckDB forbids opening a second handle to the same file with a
    # different config in one process, so we always go through the writer
    # connection. The `_validate_adhoc` allowlist is the safety boundary.
    owned = False
    if store is None:
        store = OpenAlexStore.open()
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
    store: OpenAlexStore | None = None,
) -> list[dict[str, Any]]:
    """Execute a guarded ad-hoc SELECT/WITH."""
    _validate_adhoc(sql)
    return _execute(sql, params, store)


def run_predefined(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    store: OpenAlexStore | None = None,
) -> list[dict[str, Any]]:
    """Execute a named predefined query with the given params."""
    if name not in PREDEFINED_QUERIES:
        message = (
            f"Unknown predefined query: {name!r}. "
            f"Known: {sorted(PREDEFINED_QUERIES)}"
        )
        raise ValueError(message)
    return _execute(PREDEFINED_QUERIES[name], params, store)
