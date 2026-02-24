from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from src.v2.graph.migrations import MigrationRunner
from src.v2.graph.models import Alias, AliasMatch, AliasSource, Edge, Entity
from src.v2.graph.schema import VALID_ALIAS_SOURCES


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _parse_json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        message = "Expected JSON object payload"
        raise TypeError(message)
    return parsed


def _normalize_alias(value: str) -> str:
    return " ".join(value.strip().split()).lower()


class GraphStore:
    def __init__(
        self,
        db_path: str,
        *,
        auto_migrate: bool = True,
        migrations_dir: Path | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._migration_runner = MigrationRunner(
            db_path=str(self._db_path),
            migrations_dir=migrations_dir,
        )
        if auto_migrate:
            self._migration_runner.apply_pending()

    def insert_entity(
        self,
        entity_type: str,
        entity_id: str,
        data: dict[str, Any],
        identifiers: dict[str, Any],
        id_source: str,
    ) -> str:
        now = _utcnow_iso()
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO entities (
                    id,
                    type,
                    data,
                    identifiers,
                    id_source,
                    provenance,
                    last_seen,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    entity_id,
                    entity_type,
                    json.dumps(data),
                    json.dumps(identifiers),
                    id_source,
                    json.dumps({}),
                    now,
                    now,
                ),
            )
        return entity_id

    def get_entity(self, entity_id: str) -> Entity | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    type,
                    data,
                    identifiers,
                    id_source,
                    provenance,
                    last_seen,
                    created_at
                FROM entities
                WHERE id = ?;
                """,
                (entity_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_entity(row)

    def get_all_entities(self) -> list[Entity]:
        return self._fetch_entities(
            """
            SELECT
                id,
                type,
                data,
                identifiers,
                id_source,
                provenance,
                last_seen,
                created_at
            FROM entities
            ORDER BY created_at ASC, id ASC;
            """,
            (),
        )

    def get_entities_by_type(self, entity_type: str) -> list[Entity]:
        return self._fetch_entities(
            """
            SELECT
                id,
                type,
                data,
                identifiers,
                id_source,
                provenance,
                last_seen,
                created_at
            FROM entities
            WHERE type = ?
            ORDER BY created_at ASC, id ASC;
            """,
            (entity_type,),
        )

    def update_entity(
        self,
        entity_id: str,
        data: dict[str, Any],
        identifiers: dict[str, Any] | None = None,
    ) -> bool:
        now = _utcnow_iso()
        with self._connect() as connection, connection:
            if identifiers is None:
                cursor = connection.execute(
                    """
                    UPDATE entities
                    SET data = ?, last_seen = ?
                    WHERE id = ?;
                    """,
                    (json.dumps(data), now, entity_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE entities
                    SET data = ?, identifiers = ?, last_seen = ?
                    WHERE id = ?;
                    """,
                    (json.dumps(data), json.dumps(identifiers), now, entity_id),
                )
        return cursor.rowcount > 0

    def delete_entity(self, entity_id: str) -> bool:
        with self._connect() as connection, connection:
            cursor = connection.execute(
                "DELETE FROM entities WHERE id = ?;",
                (entity_id,),
            )
        return cursor.rowcount > 0

    def insert_edge(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        data: dict[str, Any] | None = None,
    ) -> str:
        edge_id = str(uuid4())
        payload = data if data is not None else {}
        now = _utcnow_iso()
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO edges (
                    id,
                    source_id,
                    target_id,
                    relation_type,
                    data,
                    provenance,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    edge_id,
                    source_id,
                    target_id,
                    relation_type,
                    json.dumps(payload),
                    json.dumps({}),
                    now,
                ),
            )
        return edge_id

    def get_edges_by_source(self, entity_id: str) -> list[Edge]:
        return self._fetch_edges(
            """
            SELECT
                id,
                source_id,
                target_id,
                relation_type,
                data,
                provenance,
                created_at
            FROM edges
            WHERE source_id = ?
            ORDER BY created_at ASC, id ASC;
            """,
            (entity_id,),
        )

    def get_edges_by_target(self, entity_id: str) -> list[Edge]:
        return self._fetch_edges(
            """
            SELECT
                id,
                source_id,
                target_id,
                relation_type,
                data,
                provenance,
                created_at
            FROM edges
            WHERE target_id = ?
            ORDER BY created_at ASC, id ASC;
            """,
            (entity_id,),
        )

    def get_edges_by_type(self, relation_type: str) -> list[Edge]:
        return self._fetch_edges(
            """
            SELECT
                id,
                source_id,
                target_id,
                relation_type,
                data,
                provenance,
                created_at
            FROM edges
            WHERE relation_type = ?
            ORDER BY created_at ASC, id ASC;
            """,
            (relation_type,),
        )

    def delete_edge(self, edge_id: str) -> bool:
        with self._connect() as connection, connection:
            cursor = connection.execute(
                "DELETE FROM edges WHERE id = ?;",
                (edge_id,),
            )
        return cursor.rowcount > 0

    def insert_alias(
        self,
        alias_string: str,
        canonical_entity_id: str,
        confidence: float,
        source: AliasSource,
    ) -> str:
        if source not in VALID_ALIAS_SOURCES:
            message = f"Invalid alias source: {source}"
            raise ValueError(message)
        if confidence < 0.0 or confidence > 1.0:
            message = f"Alias confidence must be between 0 and 1, got {confidence}"
            raise ValueError(message)

        normalized_alias = _normalize_alias(alias_string)
        alias_id = str(uuid4())
        now = _utcnow_iso()

        with self._connect() as connection, connection:
            existing = connection.execute(
                """
                SELECT id, canonical_entity_id
                FROM aliases
                WHERE alias_normalized = ?;
                """,
                (normalized_alias,),
            ).fetchone()
            if existing is not None:
                existing_entity_id = str(existing["canonical_entity_id"])
                if existing_entity_id != canonical_entity_id:
                    message = (
                        f"Alias '{alias_string}' already mapped to entity "
                        f"'{existing_entity_id}'"
                    )
                    raise ValueError(message)
                return str(existing["id"])

            connection.execute(
                """
                INSERT INTO aliases (
                    id,
                    alias_string,
                    alias_normalized,
                    canonical_entity_id,
                    confidence,
                    source,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    alias_id,
                    alias_string,
                    normalized_alias,
                    canonical_entity_id,
                    confidence,
                    source,
                    now,
                ),
            )

        return alias_id

    def lookup_alias(self, alias_string: str) -> AliasMatch | None:
        normalized_alias = _normalize_alias(alias_string)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    alias_string,
                    canonical_entity_id,
                    confidence,
                    source
                FROM aliases
                WHERE alias_normalized = ?;
                """,
                (normalized_alias,),
            ).fetchone()
        if row is None:
            return None

        source = cast("AliasSource", str(row["source"]))
        return AliasMatch(
            alias_id=str(row["id"]),
            alias_string=str(row["alias_string"]),
            canonical_entity_id=str(row["canonical_entity_id"]),
            confidence=float(row["confidence"]),
            source=source,
        )

    def get_aliases_for_entity(self, entity_id: str) -> list[Alias]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    alias_string,
                    canonical_entity_id,
                    confidence,
                    source,
                    created_at
                FROM aliases
                WHERE canonical_entity_id = ?
                ORDER BY alias_string ASC;
                """,
                (entity_id,),
            ).fetchall()
        return [self._row_to_alias(row) for row in rows]

    def delete_alias(self, alias_id: str) -> bool:
        with self._connect() as connection, connection:
            cursor = connection.execute(
                "DELETE FROM aliases WHERE id = ?;",
                (alias_id,),
            )
        return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        return connection

    def _fetch_entities(
        self,
        sql: str,
        parameters: tuple[Any, ...],
    ) -> list[Entity]:
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._row_to_entity(row) for row in rows]

    def _fetch_edges(self, sql: str, parameters: tuple[Any, ...]) -> list[Edge]:
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._row_to_edge(row) for row in rows]

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> Entity:
        return Entity(
            id=str(row["id"]),
            type=str(row["type"]),
            data=_parse_json_object(str(row["data"])),
            identifiers=_parse_json_object(str(row["identifiers"])),
            id_source=str(row["id_source"]),
            provenance=_parse_json_object(str(row["provenance"])),
            last_seen=_parse_timestamp(str(row["last_seen"])),
            created_at=_parse_timestamp(str(row["created_at"])),
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> Edge:
        return Edge(
            id=str(row["id"]),
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            relation_type=str(row["relation_type"]),
            data=_parse_json_object(str(row["data"])),
            provenance=_parse_json_object(str(row["provenance"])),
            created_at=_parse_timestamp(str(row["created_at"])),
        )

    @staticmethod
    def _row_to_alias(row: sqlite3.Row) -> Alias:
        source = cast("AliasSource", str(row["source"]))
        return Alias(
            id=str(row["id"]),
            alias_string=str(row["alias_string"]),
            canonical_entity_id=str(row["canonical_entity_id"]),
            confidence=float(row["confidence"]),
            source=source,
            created_at=_parse_timestamp(str(row["created_at"])),
        )
