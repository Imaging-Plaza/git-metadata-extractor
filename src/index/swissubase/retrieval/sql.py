"""Read-only SQL surface over the SWISSUbase DuckDB.

Two entrypoints:

- :func:`run_predefined` — parametrized canned queries.
- :func:`run_adhoc` — guarded SELECT/WITH only, with a forbidden-keyword regex.
"""

from __future__ import annotations

import re
from typing import Any

from src.index.swissubase.storage.duckdb_store import SwissubaseStore

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
        "SELECT 'studies' AS entity, COUNT(*) AS n FROM studies "
        "UNION ALL SELECT 'studies_in_scope', COUNT(*) FROM studies WHERE affiliation_match "
        "UNION ALL SELECT 'datasets', COUNT(*) FROM datasets "
        "UNION ALL SELECT 'persons', COUNT(*) FROM persons "
        "UNION ALL SELECT 'institutions', COUNT(*) FROM institutions "
        "UNION ALL SELECT 'study_persons', COUNT(*) FROM study_persons "
        "UNION ALL SELECT 'study_institutions', COUNT(*) FROM study_institutions "
        "UNION ALL SELECT 'chunks', COUNT(*) FROM chunks"
    ),
    "in_scope_studies": (
        "SELECT study_id, ref, title, progress, source_url "
        "FROM studies WHERE affiliation_match "
        "ORDER BY end_date DESC NULLS LAST, ref "
        "LIMIT $limit"
    ),
    "studies_by_institution": (
        "SELECT s.study_id, s.ref, s.title, s.source_url "
        "FROM study_institutions si "
        "JOIN studies s ON s.study_id = si.study_id "
        "WHERE si.institution_key = $institution_key "
        "ORDER BY s.end_date DESC NULLS LAST, s.ref "
        "LIMIT $limit"
    ),
    "studies_by_person": (
        "SELECT s.study_id, s.ref, s.title, sp.role, s.source_url "
        "FROM study_persons sp "
        "JOIN studies s ON s.study_id = sp.study_id "
        "WHERE sp.person_key = $person_key "
        "ORDER BY s.end_date DESC NULLS LAST, s.ref "
        "LIMIT $limit"
    ),
    "top_institutions_in_scope": (
        "SELECT i.institution_key, i.name, COUNT(*) AS studies "
        "FROM study_institutions si "
        "JOIN studies s ON s.study_id = si.study_id "
        "JOIN institutions i ON i.institution_key = si.institution_key "
        "WHERE s.affiliation_match "
        "GROUP BY i.institution_key, i.name "
        "ORDER BY studies DESC LIMIT $limit"
    ),
    "top_persons_in_scope": (
        "SELECT p.person_key, p.display_name, COUNT(*) AS studies "
        "FROM study_persons sp "
        "JOIN studies s ON s.study_id = sp.study_id "
        "JOIN persons p ON p.person_key = sp.person_key "
        "WHERE s.affiliation_match "
        "GROUP BY p.person_key, p.display_name "
        "ORDER BY studies DESC LIMIT $limit"
    ),
    "studies_by_discipline": (
        "SELECT main_discipline, COUNT(*) AS n FROM studies "
        "WHERE affiliation_match "
        "GROUP BY main_discipline ORDER BY n DESC"
    ),
}


def _row_to_dict(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _execute(
    sql: str,
    params: dict[str, Any] | None,
    store: SwissubaseStore | None,
) -> list[dict[str, Any]]:
    owned = False
    if store is None:
        store = SwissubaseStore.open()
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
    store: SwissubaseStore | None = None,
) -> list[dict[str, Any]]:
    _validate_adhoc(sql)
    return _execute(sql, params, store)


def run_predefined(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    store: SwissubaseStore | None = None,
) -> list[dict[str, Any]]:
    if name not in PREDEFINED_QUERIES:
        message = (
            f"Unknown predefined query: {name!r}. "
            f"Known: {sorted(PREDEFINED_QUERIES)}"
        )
        raise ValueError(message)
    return _execute(PREDEFINED_QUERIES[name], params, store)
