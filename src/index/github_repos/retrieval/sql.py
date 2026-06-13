"""Read-only SQL surface over the GitHub DuckDB.

Two entrypoints:

- `run_predefined()` — parametrized canned queries.
- `run_adhoc()` — guarded SELECT/WITH only, with a forbidden-keyword regex.
"""

from __future__ import annotations

import re
from typing import Any

from src.index.github_repos.storage.duckdb_store import GitHubReposStore

INVALID_QUERY_PREFIX_ERROR = "Only SELECT/WITH queries are allowed"
FORBIDDEN_KEYWORD_ERROR = "Forbidden keyword in query: {kw}"

_ALLOWED_PREFIXES = ("select", "with")
_FORBIDDEN_KEYWORDS = (
    "attach", "copy", "pragma", "install", "load", "export", "import",
    "create", "drop", "alter", "insert", "update", "delete", "truncate",
    "recursive",
)
_KEYWORD_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)

# Hard cap on rows returned by an ad-hoc query — bounds result-set DoS.
_ADHOC_MAX_ROWS = 1000


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
    store: GitHubReposStore | None,
) -> list[dict[str, Any]]:
    owned = False
    if store is None:
        store = GitHubReposStore.open()
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
    store: GitHubReposStore | None = None,
) -> list[dict[str, Any]]:
    _validate_adhoc(sql)
    # Sandbox the ad-hoc query: `enable_external_access=false` makes DuckDB
    # built-ins like read_csv_auto/read_parquet/glob/read_text raise
    # PermissionException, closing the arbitrary-local-file-read (LFI) hole,
    # and `memory_limit` plus the _ADHOC_MAX_ROWS fetch cap bound a result/CPU
    # DoS. We deliberately reuse the EXISTING store.connect() handle rather
    # than opening a sandboxed second connection: DuckDB forbids a second
    # handle to the same file with a different config in one process, so a
    # second connection would trip the access-mode conflict. SET on the live
    # connection avoids that entirely.
    owned = False
    if store is None:
        store = GitHubReposStore.open()
        owned = True
    try:
        con = store.connect()
        con.execute("SET enable_external_access=false")
        con.execute("SET memory_limit='512MB'")
        cur = con.execute(sql, params or {})
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(_ADHOC_MAX_ROWS)
        return [dict(zip(cols, r, strict=False)) for r in rows]
    finally:
        if owned:
            store.close()


def run_predefined(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    store: GitHubReposStore | None = None,
) -> list[dict[str, Any]]:
    if name not in PREDEFINED_QUERIES:
        message = (
            f"Unknown predefined query: {name!r}. "
            f"Known: {sorted(PREDEFINED_QUERIES)}"
        )
        raise ValueError(message)
    return _execute(PREDEFINED_QUERIES[name], params, store)
