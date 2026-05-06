"""Read-only SQL surface over the RenkuLab DuckDB.

Two entrypoints:

- `run_predefined()` — parametrized canned queries.
- `run_adhoc()` — guarded SELECT/WITH only, with a forbidden-keyword regex.
"""

from __future__ import annotations

import re
from typing import Any

from src.index.renkulab.storage.duckdb_store import RenkulabStore

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
    "count_by_entity": (
        "SELECT 'projects' AS entity, COUNT(*) AS n FROM projects "
        "UNION ALL SELECT 'groups', COUNT(*) FROM groups "
        "UNION ALL SELECT 'users', COUNT(*) FROM users "
        "UNION ALL SELECT 'data_connectors', COUNT(*) FROM data_connectors "
        "UNION ALL SELECT 'group_members', COUNT(*) FROM group_members "
        "UNION ALL SELECT 'project_members', COUNT(*) FROM project_members "
        "UNION ALL SELECT 'chunks', COUNT(*) FROM chunks"
    ),
    "projects_by_visibility": (
        "SELECT visibility, COUNT(*) AS n FROM projects "
        "GROUP BY visibility ORDER BY n DESC"
    ),
    "data_connectors_by_storage_type": (
        "SELECT storage_type, COUNT(*) AS n FROM data_connectors "
        "GROUP BY storage_type ORDER BY n DESC"
    ),
    "top_groups_by_member_count": (
        "SELECT g.group_id, g.slug, g.name, COUNT(gm.user_id) AS members "
        "FROM groups g "
        "LEFT JOIN group_members gm ON gm.group_id = g.group_id "
        "GROUP BY g.group_id, g.slug, g.name "
        "ORDER BY members DESC LIMIT $limit"
    ),
    "top_groups_by_project_count": (
        "SELECT g.slug AS group_slug, COUNT(*) AS projects "
        "FROM projects p JOIN groups g "
        "  ON p.namespace = g.slug "
        "GROUP BY g.slug ORDER BY projects DESC LIMIT $limit"
    ),
    "recent_projects": (
        "SELECT project_id, slug, name, namespace, creation_date, visibility "
        "FROM projects "
        "WHERE creation_date IS NOT NULL "
        "ORDER BY creation_date DESC LIMIT $limit"
    ),
    "data_connectors_by_namespace": (
        "SELECT namespace, COUNT(*) AS n FROM data_connectors "
        "WHERE namespace IS NOT NULL "
        "GROUP BY namespace ORDER BY n DESC LIMIT $limit"
    ),
    "user_projects": (
        "SELECT p.project_id, p.slug, p.name, p.namespace, pm.role "
        "FROM project_members pm JOIN projects p "
        "  ON p.project_id = pm.project_id "
        "WHERE pm.user_id = $user_id "
        "ORDER BY p.creation_date DESC NULLS LAST LIMIT $limit"
    ),
    "user_groups": (
        "SELECT g.group_id, g.slug, g.name, gm.role "
        "FROM group_members gm JOIN groups g "
        "  ON g.group_id = gm.group_id "
        "WHERE gm.user_id = $user_id "
        "ORDER BY g.creation_date DESC NULLS LAST LIMIT $limit"
    ),
    "projects_by_keyword": (
        "SELECT project_id, slug, name, namespace "
        "FROM projects "
        "WHERE list_contains(CAST(keywords_json AS VARCHAR[]), $keyword) "
        "ORDER BY creation_date DESC NULLS LAST LIMIT $limit"
    ),
}


def _row_to_dict(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _execute(
    sql: str,
    params: dict[str, Any] | None,
    store: RenkulabStore | None,
) -> list[dict[str, Any]]:
    owned = False
    if store is None:
        store = RenkulabStore.open()
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
    store: RenkulabStore | None = None,
) -> list[dict[str, Any]]:
    _validate_adhoc(sql)
    return _execute(sql, params, store)


def run_predefined(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    store: RenkulabStore | None = None,
) -> list[dict[str, Any]]:
    if name not in PREDEFINED_QUERIES:
        message = (
            f"Unknown predefined query: {name!r}. "
            f"Known: {sorted(PREDEFINED_QUERIES)}"
        )
        raise ValueError(message)
    return _execute(PREDEFINED_QUERIES[name], params, store)
