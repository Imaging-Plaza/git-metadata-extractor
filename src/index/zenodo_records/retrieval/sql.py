"""Read-only SQL surface over the Zenodo DuckDB.

Two entrypoints:

- `run_predefined()` — parametrized canned queries.
- `run_adhoc()` — guarded SELECT/WITH only, with a forbidden-keyword regex.
"""

from __future__ import annotations

import re
from typing import Any

from src.index.zenodo_records.storage.duckdb_store import ZenodoRecordsStore

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
    "count_by_entity": (
        "SELECT 'records' AS entity, COUNT(*) AS n FROM records "
        "UNION ALL SELECT 'creators', COUNT(*) FROM creators "
        "UNION ALL SELECT 'communities', COUNT(*) FROM communities "
        "UNION ALL SELECT 'record_creators', COUNT(*) FROM record_creators "
        "UNION ALL SELECT 'record_communities', COUNT(*) FROM record_communities "
        "UNION ALL SELECT 'files', COUNT(*) FROM files "
        "UNION ALL SELECT 'chunks', COUNT(*) FROM chunks"
    ),
    "count_by_community": (
        "SELECT rc.community_id, COUNT(*) AS records "
        "FROM record_communities rc "
        "GROUP BY rc.community_id ORDER BY records DESC"
    ),
    "count_by_resource_type": (
        "SELECT resource_type, COUNT(*) AS n FROM records "
        "GROUP BY resource_type ORDER BY n DESC"
    ),
    "count_by_access_right": (
        "SELECT access_right, COUNT(*) AS n FROM records "
        "GROUP BY access_right ORDER BY n DESC"
    ),
    "top_recent_records": (
        "SELECT zenodo_id, doi, title, publication_date, resource_type "
        "FROM records "
        "WHERE publication_date IS NOT NULL "
        "ORDER BY publication_date DESC, title "
        "LIMIT $limit"
    ),
    "records_by_creator": (
        "SELECT r.zenodo_id, r.title, r.publication_date, r.doi "
        "FROM record_creators rc "
        "JOIN records r ON r.zenodo_id = rc.record_id "
        "WHERE rc.creator_key = $creator_key "
        "ORDER BY r.publication_date DESC NULLS LAST "
        "LIMIT $limit"
    ),
    "records_by_keyword": (
        "SELECT zenodo_id, title, publication_date "
        "FROM records "
        "WHERE list_contains(CAST(keywords_json AS VARCHAR[]), $keyword) "
        "ORDER BY publication_date DESC NULLS LAST "
        "LIMIT $limit"
    ),
}


def _row_to_dict(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _execute(
    sql: str,
    params: dict[str, Any] | None,
    store: ZenodoRecordsStore | None,
) -> list[dict[str, Any]]:
    owned = False
    if store is None:
        store = ZenodoRecordsStore.open()
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
    store: ZenodoRecordsStore | None = None,
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
        store = ZenodoRecordsStore.open()
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
    store: ZenodoRecordsStore | None = None,
) -> list[dict[str, Any]]:
    if name not in PREDEFINED_QUERIES:
        message = (
            f"Unknown predefined query: {name!r}. "
            f"Known: {sorted(PREDEFINED_QUERIES)}"
        )
        raise ValueError(message)
    return _execute(PREDEFINED_QUERIES[name], params, store)
