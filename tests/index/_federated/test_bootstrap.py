"""Tests for the bootstrap-index utility."""

from __future__ import annotations

from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    import pytest

from src.index._federated.bootstrap import bootstrap_all, discover_store_names


def test_discover_excludes_underscore_and_legacy() -> None:
    """discover_store_names() returns real stores and excludes underscore dirs & huggingface."""
    names = discover_store_names()

    # Must be sorted
    assert names == sorted(names)

    # Known stores must be included
    assert "dockerhub" in names
    assert "gitlab_epfl_projects" in names
    assert "github_repos" in names

    # Legacy monolith must be excluded
    assert "huggingface" not in names

    # Underscore-prefixed dirs must be excluded
    for name in names:
        assert not name.startswith("_"), f"Underscore dir leaked: {name!r}"


def test_bootstrap_creates_duckdb(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    """bootstrap_all() creates DuckDB files with expected tables for each store."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    result = bootstrap_all(only=["dockerhub", "gitlab_epfl_projects"])

    # Both stores must succeed (status "created" or "exists", not "error: ...")
    assert result["dockerhub"] in {"created", "exists"}, f"dockerhub status: {result['dockerhub']}"
    assert result["gitlab_epfl_projects"] in {"created", "exists"}, (
        f"gitlab_epfl_projects status: {result['gitlab_epfl_projects']}"
    )

    # DuckDB files must exist on disk
    dockerhub_db = tmp_path / "dockerhub" / "duckdb" / "dockerhub.duckdb"
    gitlab_db = tmp_path / "gitlab_epfl_projects" / "duckdb" / "gitlab_epfl_projects.duckdb"
    assert dockerhub_db.exists(), f"Missing: {dockerhub_db}"
    assert gitlab_db.exists(), f"Missing: {gitlab_db}"

    # DuckDB files must have expected tables
    with duckdb.connect(str(dockerhub_db), read_only=True) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'",
            ).fetchall()
        }
        assert "images" in tables, f"dockerhub tables: {tables}"

    with duckdb.connect(str(gitlab_db), read_only=True) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'",
            ).fetchall()
        }
        assert "projects" in tables, f"gitlab_epfl_projects tables: {tables}"


_GITLAB_GROUPS_AND_USERS_LEAVES = [
    "gitlab_epfl_groups", "gitlab_ethz_groups", "gitlab_datascience_groups",
    "gitlab_epfl_users", "gitlab_ethz_users", "gitlab_datascience_users",
]


def test_gitlab_groups_and_users_leaves_bootstrap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory,
) -> None:
    """The gitlab groups + users leaves use the leaf opener convention and must
    bootstrap (not 'skipped: no duckdb store')."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    result = bootstrap_all(only=_GITLAB_GROUPS_AND_USERS_LEAVES)

    for name in _GITLAB_GROUPS_AND_USERS_LEAVES:
        assert result[name] in {"created", "exists"}, f"{name} status: {result[name]}"
        assert result[name] != "skipped: no duckdb store"

    # The users stores expose a `users` table; groups expose a `groups` table.
    users_db = tmp_path / "gitlab_epfl_users" / "duckdb" / "gitlab_epfl_users.duckdb"
    assert users_db.exists(), f"Missing: {users_db}"
    with duckdb.connect(str(users_db), read_only=True) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'",
            ).fetchall()
        }
        assert "users" in tables, f"gitlab_epfl_users tables: {tables}"


def test_bootstrap_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    """Running bootstrap_all() twice: first run → 'created', second run → 'exists'."""
    monkeypatch.setenv("INDEX_DATA_DIR", str(tmp_path))

    first = bootstrap_all(only=["dockerhub"])
    assert first["dockerhub"] in {"created", "exists"}, f"first run: {first['dockerhub']}"

    second = bootstrap_all(only=["dockerhub"])
    assert second["dockerhub"] == "exists", f"second run should be 'exists', got: {second['dockerhub']}"
