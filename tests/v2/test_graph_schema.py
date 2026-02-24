from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.v2.graph.migrations import MigrationRunner

EXPECTED_MIGRATION_COUNT = 2


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table';",
        ).fetchall()
    return {str(row[0]) for row in rows}


def _table_columns(db_path: Path, table_name: str) -> dict[str, str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table_name});").fetchall()
    return {str(row[1]): str(row[2]).upper() for row in rows}


def test_initial_migration_creates_all_graph_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_schema.db"
    runner = MigrationRunner(str(db_path))

    applied = runner.apply_pending()

    assert applied == 1
    assert runner.get_current_version() == 1
    assert {
        "entities",
        "edges",
        "aliases",
        "intermediates",
        "runs",
        "schema_version",
    }.issubset(_table_names(db_path))


def test_reapplying_migrations_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_schema_noop.db"
    runner = MigrationRunner(str(db_path))

    first_applied = runner.apply_pending()
    second_applied = runner.apply_pending()

    assert first_applied == 1
    assert second_applied == 0
    assert runner.get_current_version() == 1


def test_graph_tables_have_expected_required_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_schema_columns.db"
    runner = MigrationRunner(str(db_path))
    runner.apply_pending()

    required_columns = {
        "entities": {
            "id": "TEXT",
            "type": "TEXT",
            "data": "TEXT",
            "identifiers": "TEXT",
            "id_source": "TEXT",
            "provenance": "TEXT",
            "last_seen": "TEXT",
            "created_at": "TEXT",
        },
        "edges": {
            "id": "TEXT",
            "source_id": "TEXT",
            "target_id": "TEXT",
            "relation_type": "TEXT",
            "data": "TEXT",
            "provenance": "TEXT",
            "created_at": "TEXT",
        },
        "aliases": {
            "id": "TEXT",
            "alias_string": "TEXT",
            "canonical_entity_id": "TEXT",
            "confidence": "REAL",
            "source": "TEXT",
            "created_at": "TEXT",
        },
        "intermediates": {
            "id": "TEXT",
            "source_url": "TEXT",
            "agent_name": "TEXT",
            "run_id": "TEXT",
            "data": "TEXT",
            "created_at": "TEXT",
        },
        "runs": {
            "id": "TEXT",
            "source_url": "TEXT",
            "detected_type": "TEXT",
            "status": "TEXT",
            "stats": "TEXT",
            "started_at": "TEXT",
            "completed_at": "TEXT",
        },
        "schema_version": {
            "version": "INTEGER",
            "applied_at": "TEXT",
        },
    }

    for table_name, expected_columns in required_columns.items():
        actual = _table_columns(db_path, table_name)
        for column_name, column_type in expected_columns.items():
            assert actual[column_name] == column_type


def test_migration_runner_enables_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_schema_fk.db"
    runner = MigrationRunner(str(db_path))

    with runner.connect() as connection:
        pragma_value = connection.execute("PRAGMA foreign_keys;").fetchone()

    assert pragma_value is not None
    assert int(pragma_value[0]) == 1


def test_migration_runner_detects_and_applies_new_migration_files(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_schema_new_migrations.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)

    initial_sql = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "v2"
        / "graph"
        / "migrations"
        / "001_initial.sql"
    )
    (migrations_dir / "001_initial.sql").write_text(
        initial_sql.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (migrations_dir / "002_probe.sql").write_text(
        "CREATE TABLE IF NOT EXISTS migration_probe (id TEXT PRIMARY KEY);\n",
        encoding="utf-8",
    )

    runner = MigrationRunner(str(db_path), migrations_dir=migrations_dir)

    applied_count = runner.apply_pending()

    assert applied_count == EXPECTED_MIGRATION_COUNT
    assert runner.get_current_version() == EXPECTED_MIGRATION_COUNT
    assert "migration_probe" in _table_names(db_path)


def test_rollback_is_blocked_by_default_for_destructive_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_schema_blocked_rollback.db"
    migrations_dir = tmp_path / "migrations_guarded"
    migrations_dir.mkdir(parents=True, exist_ok=True)

    initial_sql = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "v2"
        / "graph"
        / "migrations"
        / "001_initial.sql"
    )
    (migrations_dir / "001_initial.sql").write_text(
        initial_sql.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (migrations_dir / "002_probe.sql").write_text(
        "CREATE TABLE IF NOT EXISTS migration_probe (id TEXT PRIMARY KEY);\n",
        encoding="utf-8",
    )

    runner = MigrationRunner(str(db_path), migrations_dir=migrations_dir)
    runner.apply_pending()

    with pytest.raises(PermissionError, match="Destructive rollback is disabled"):
        runner.rollback_to(1)


def test_rollback_is_allowed_when_explicitly_enabled(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_schema_allowed_rollback.db"
    migrations_dir = tmp_path / "migrations_enabled"
    migrations_dir.mkdir(parents=True, exist_ok=True)

    initial_sql = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "v2"
        / "graph"
        / "migrations"
        / "001_initial.sql"
    )
    (migrations_dir / "001_initial.sql").write_text(
        initial_sql.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (migrations_dir / "002_probe.sql").write_text(
        "CREATE TABLE IF NOT EXISTS migration_probe (id TEXT PRIMARY KEY);\n",
        encoding="utf-8",
    )

    runner = MigrationRunner(
        str(db_path),
        migrations_dir=migrations_dir,
        allow_destructive_rollback=True,
    )
    runner.apply_pending()

    rolled_back_version = runner.rollback_to(1)

    assert rolled_back_version == 1
    assert runner.get_current_version() == 1
