"""Read-only SQL surface over the ORCID DuckDB.

Mirrors `src/index/openalex/retrieval/sql.py`: predefined parametrized
queries plus a guarded ad-hoc SELECT/WITH path.
"""

from __future__ import annotations

import re
from typing import Any

from src.index.orcid.storage.duckdb_store import OrcidStore

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
        "SELECT 'persons' AS entity, COUNT(*) AS n FROM persons "
        "UNION ALL SELECT 'persons_in_scope', COUNT(*) FROM persons WHERE in_scope "
        "UNION ALL SELECT 'employments', COUNT(*) FROM employments "
        "UNION ALL SELECT 'educations', COUNT(*) FROM educations "
        "UNION ALL SELECT 'seeds', COUNT(*) FROM seeds "
        "UNION ALL SELECT 'chunks', COUNT(*) FROM chunks"
    ),
    "scope_summary": (
        "SELECT discovered_via, in_scope, COUNT(*) AS n "
        "FROM persons GROUP BY discovered_via, in_scope ORDER BY discovered_via, in_scope"
    ),
    "in_scope_persons": (
        "SELECT orcid_id, display_name, scope_reason FROM persons "
        "WHERE in_scope = TRUE ORDER BY family_name, given_name LIMIT $limit"
    ),
    "employments_by_org": (
        "SELECT organization, COUNT(*) AS n_persons "
        "FROM employments e JOIN persons p ON p.orcid_id = e.orcid_id "
        "WHERE p.in_scope = TRUE "
        "GROUP BY organization ORDER BY n_persons DESC LIMIT $limit"
    ),
    "person_employments": (
        "SELECT e.* FROM employments e "
        "WHERE e.orcid_id = $orcid_id ORDER BY e.seq"
    ),
}


def _row_to_dict(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _execute(
    sql: str,
    params: dict[str, Any] | None,
    store: OrcidStore | None,
) -> list[dict[str, Any]]:
    owned = False
    if store is None:
        store = OrcidStore.open()
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
    store: OrcidStore | None = None,
) -> list[dict[str, Any]]:
    _validate_adhoc(sql)
    return _execute(sql, params, store)


def run_predefined(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    store: OrcidStore | None = None,
) -> list[dict[str, Any]]:
    if name not in PREDEFINED_QUERIES:
        message = (
            f"Unknown predefined query: {name!r}. "
            f"Known: {sorted(PREDEFINED_QUERIES)}"
        )
        raise ValueError(message)
    return _execute(PREDEFINED_QUERIES[name], params, store)
