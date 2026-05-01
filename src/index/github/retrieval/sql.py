"""Read-only SQL surface over the GitHub DuckDB.

Two entrypoints:

- `run_predefined()` — parametrized canned queries.
- `run_adhoc()` — guarded SELECT/WITH only, with a forbidden-keyword regex.
"""

from __future__ import annotations

import re
from typing import Any

from src.index.github.storage.duckdb_store import GitHubStore

INVALID_QUERY_PREFIX_ERROR = "Only SELECT/WITH queries are allowed"
FORBIDDEN_KEYWORD_ERROR = "Forbidden keyword in query: {kw}"

_ALLOWED_PREFIXES = ("select", "with")
_FORBIDDEN_KEYWORDS = (
    "attach", "copy", "pragma", "install", "load", "export", "import",
    "create", "drop", "alter", "insert", "update", "delete", "truncate",
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
    "count_repos": "SELECT COUNT(*) AS n FROM repos",
    "count_by_entity": (
        "SELECT 'repos' AS entity, COUNT(*) AS n FROM repos "
        "UNION ALL SELECT 'chunks', COUNT(*) FROM chunks"
    ),
    "count_by_owner": (
        "SELECT owner, COUNT(*) AS n FROM repos "
        "GROUP BY owner ORDER BY n DESC"
    ),
    "count_by_language": (
        "SELECT primary_language, COUNT(*) AS n FROM repos "
        "GROUP BY primary_language ORDER BY n DESC"
    ),
    "count_by_license": (
        "SELECT license_spdx, COUNT(*) AS n FROM repos "
        "GROUP BY license_spdx ORDER BY n DESC"
    ),
    "top_starred": (
        "SELECT repo_id, owner, name, primary_language, "
        "       stargazers_count, pushed_at "
        "FROM repos "
        "ORDER BY stargazers_count DESC, repo_id "
        "LIMIT $limit"
    ),
    "recently_pushed": (
        "SELECT repo_id, primary_language, stargazers_count, pushed_at "
        "FROM repos "
        "WHERE pushed_at IS NOT NULL "
        "ORDER BY pushed_at DESC, repo_id "
        "LIMIT $limit"
    ),
    "archived_repos": (
        "SELECT repo_id, primary_language, stargazers_count, pushed_at "
        "FROM repos "
        "WHERE is_archived "
        "ORDER BY pushed_at DESC NULLS LAST"
    ),
    "repos_by_owner": (
        "SELECT repo_id, primary_language, stargazers_count, pushed_at "
        "FROM repos "
        "WHERE owner = $owner "
        "ORDER BY stargazers_count DESC, repo_id"
    ),
}


def _row_to_dict(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _execute(
    sql: str,
    params: dict[str, Any] | None,
    store: GitHubStore | None,
) -> list[dict[str, Any]]:
    owned = False
    if store is None:
        store = GitHubStore.open()
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
    store: GitHubStore | None = None,
) -> list[dict[str, Any]]:
    _validate_adhoc(sql)
    return _execute(sql, params, store)


def run_predefined(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    store: GitHubStore | None = None,
) -> list[dict[str, Any]]:
    if name not in PREDEFINED_QUERIES:
        message = (
            f"Unknown predefined query: {name!r}. "
            f"Known: {sorted(PREDEFINED_QUERIES)}"
        )
        raise ValueError(message)
    return _execute(PREDEFINED_QUERIES[name], params, store)
