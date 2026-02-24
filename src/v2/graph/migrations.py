from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.v2.graph.schema import DROP_ALL_TABLES_STATEMENTS, SCHEMA_VERSION_TABLE_SQL

_MIGRATION_FILENAME_PATTERN = re.compile(r"^(?P<version>\d{3})_.+\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    path: Path


class MigrationRunner:
    def __init__(self, db_path: str, migrations_dir: Path | None = None) -> None:
        self._db_path = Path(db_path)
        default_dir = Path(__file__).resolve().parent / "migrations"
        self._migrations_dir = migrations_dir if migrations_dir is not None else default_dir

    def apply_pending(self) -> int:
        migrations = self._discover_migrations()
        if not migrations:
            return 0

        applied_count = 0
        with self._connect() as connection:
            self._ensure_schema_version_table(connection)
            current_version = self._get_current_version(connection)
            pending = [migration for migration in migrations if migration.version > current_version]
            for migration in pending:
                self._apply_migration(connection, migration)
                applied_count += 1
        return applied_count

    def get_current_version(self) -> int:
        with self._connect() as connection:
            self._ensure_schema_version_table(connection)
            return self._get_current_version(connection)

    def rollback_to(self, version: int) -> int:
        if version < 0:
            message = f"Rollback version must be >= 0, got {version}"
            raise ValueError(message)

        migrations = self._discover_migrations()
        max_available = migrations[-1].version if migrations else 0
        if version > max_available:
            message = f"Rollback version {version} exceeds available migration {max_available}"
            raise ValueError(message)

        with self._connect() as connection:
            self._ensure_schema_version_table(connection)
            current_version = self._get_current_version(connection)
            if version >= current_version:
                return current_version

            with connection:
                for drop_statement in DROP_ALL_TABLES_STATEMENTS:
                    connection.execute(drop_statement)
                connection.execute(SCHEMA_VERSION_TABLE_SQL)

            for migration in migrations:
                if migration.version > version:
                    break
                self._apply_migration(connection, migration)

        return version

    def connect(self) -> sqlite3.Connection:
        return self._connect()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        return connection

    def _discover_migrations(self) -> list[Migration]:
        if not self._migrations_dir.exists():
            return []

        migrations: list[Migration] = []
        for path in sorted(self._migrations_dir.glob("*.sql")):
            match = _MIGRATION_FILENAME_PATTERN.match(path.name)
            if match is None:
                message = f"Invalid migration filename: {path.name}"
                raise ValueError(message)
            migrations.append(Migration(version=int(match.group("version")), path=path))

        versions = [migration.version for migration in migrations]
        if len(versions) != len(set(versions)):
            message = "Duplicate migration version detected"
            raise ValueError(message)

        return migrations

    @staticmethod
    def _ensure_schema_version_table(connection: sqlite3.Connection) -> None:
        with connection:
            connection.execute(SCHEMA_VERSION_TABLE_SQL)

    @staticmethod
    def _get_current_version(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_version;").fetchone()
        if row is None:
            return 0
        return int(row["version"])

    @staticmethod
    def _apply_migration(connection: sqlite3.Connection, migration: Migration) -> None:
        sql_script = migration.path.read_text(encoding="utf-8")
        with connection:
            connection.executescript(sql_script)
            connection.execute(
                "INSERT INTO schema_version (version) VALUES (?);",
                (migration.version,),
            )
