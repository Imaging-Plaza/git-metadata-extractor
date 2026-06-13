"""Security regression tests for the ad-hoc `/query` SQL sandbox.

Covers audit findings `query-adhoc-lfi-duckdb` (arbitrary local file read via
DuckDB built-ins like `read_csv_auto`/`glob`) and `query-adhoc-resource-dos`
(unbounded result set / `WITH RECURSIVE` CPU blowup).

`run_adhoc` runs guarded SELECT/WITH on the store's read-write connection. The
fix sandboxes that same connection with `SET enable_external_access=false`
(blocks filesystem built-ins) and a row cap, and rejects `RECURSIVE` at the
validation layer. These tests assert each leg holds across the standalone
index apps.
"""

from __future__ import annotations

import importlib

import pytest

# (app key, sql module path, Store import path, Store class, db filename)
_APPS = [
    (
        "github_repos",
        "src.index.github_repos.retrieval.sql",
        "src.index.github_repos.storage.duckdb_store",
        "GitHubReposStore",
        "github_repos.duckdb",
    ),
    (
        "orcid",
        "src.index.orcid.retrieval.sql",
        "src.index.orcid.storage.duckdb_store",
        "OrcidStore",
        "orcid.duckdb",
    ),
]


def _open_store(app, tmp_path):
    """Open the app's Store on a fresh tmp_path DB (bootstrapped)."""
    _, _sql_path, store_path, store_cls_name, db_name = app
    store_mod = importlib.import_module(store_path)
    store_cls = getattr(store_mod, store_cls_name)
    db_path = tmp_path / db_name
    # `.open(path)` bootstraps the schema and returns a connected store.
    return store_cls.open(db_path)


def _sql(app):
    return importlib.import_module(app[1])


@pytest.mark.parametrize("app", _APPS, ids=[a[0] for a in _APPS])
def test_adhoc_normal_select_returns_dict_rows(app, tmp_path):
    store = _open_store(app, tmp_path)
    sql = _sql(app)
    try:
        rows = sql.run_adhoc("SELECT 1 AS n, 'hi' AS label", store=store)
        assert rows == [{"n": 1, "label": "hi"}]
        # The `chunks` table exists in every app schema; an empty-table
        # SELECT must still return a (possibly empty) list of dict rows.
        empty = sql.run_adhoc("SELECT chunk_id FROM chunks", store=store)
        assert empty == []
    finally:
        store.close()


@pytest.mark.parametrize("app", _APPS, ids=[a[0] for a in _APPS])
def test_adhoc_lfi_via_read_csv_auto_blocked(app, tmp_path):
    """`query-adhoc-lfi-duckdb`: filesystem built-ins must be denied."""
    store = _open_store(app, tmp_path)
    sql = _sql(app)
    try:
        # Passes the SELECT-prefix + keyword guard, so the only thing that can
        # stop it reading /etc/hostname is the runtime sandbox.
        with pytest.raises(Exception) as exc_info:  # noqa: PT011 - PermissionException is internal
            sql.run_adhoc(
                "SELECT * FROM read_csv_auto('/etc/hostname')",
                store=store,
            )
        # DuckDB raises PermissionException once external access is disabled.
        assert "PermissionException" in type(exc_info.value).__name__ or (
            "external access" in str(exc_info.value).lower()
        )
    finally:
        store.close()


@pytest.mark.parametrize("app", _APPS, ids=[a[0] for a in _APPS])
def test_adhoc_glob_blocked(app, tmp_path):
    """`query-adhoc-lfi-duckdb`: directory listing via glob must be denied."""
    store = _open_store(app, tmp_path)
    sql = _sql(app)
    try:
        with pytest.raises(Exception):  # noqa: B017, PT011 - PermissionException is internal
            sql.run_adhoc("SELECT * FROM glob('/etc/*')", store=store)
    finally:
        store.close()


@pytest.mark.parametrize("app", _APPS, ids=[a[0] for a in _APPS])
def test_adhoc_with_recursive_rejected(app, tmp_path):
    """`query-adhoc-resource-dos`: WITH RECURSIVE is blocked pre-execution.

    It would otherwise slip past the prefix check (starts with `WITH`), so the
    `recursive` keyword must be in the blocklist and raise ValueError.
    """
    store = _open_store(app, tmp_path)
    sql = _sql(app)
    try:
        with pytest.raises(ValueError, match=r"(?i)recursive"):
            sql.run_adhoc(
                "WITH RECURSIVE r(n) AS ("
                "  SELECT 1 UNION ALL SELECT n + 1 FROM r"
                ") SELECT n FROM r",
                store=store,
            )
    finally:
        store.close()


def test_adhoc_row_cap_enforced(tmp_path):
    """`query-adhoc-resource-dos`: results are capped at `_ADHOC_MAX_ROWS`.

    Uses github_repos: generate > _ADHOC_MAX_ROWS rows entirely in SQL
    (range()) so the cap is exercised without depending on table contents.
    """
    app = _APPS[0]  # github_repos
    store = _open_store(app, tmp_path)
    sql = _sql(app)
    try:
        max_rows = sql._ADHOC_MAX_ROWS  # noqa: SLF001 - cap constant under test
        over = max_rows + 500
        rows = sql.run_adhoc(
            f"SELECT i FROM range({over}) t(i)",  # noqa: S608 - integer literal, not user input
            store=store,
        )
        assert len(rows) == max_rows
    finally:
        store.close()
