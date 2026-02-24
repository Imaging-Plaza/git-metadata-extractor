from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, TypeVar, cast
from uuid import uuid4

from src.v2.graph.concurrency import with_write_retry
from src.v2.graph.merge import MergePolicy, MergeResult
from src.v2.graph.migrations import MigrationRunner
from src.v2.graph.models import (
    Alias,
    AliasMatch,
    AliasSource,
    Edge,
    Entity,
    ProvenanceEntry,
    Run,
)
from src.v2.graph.provenance import ProvenanceTracker
from src.v2.graph.rdf_sync import RDFGraphSync
from src.v2.graph.schema import VALID_ALIAS_SOURCES

if TYPE_CHECKING:
    from rdflib import Graph

DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5000
DEFAULT_WRITE_RETRY_COUNT = 3
DEFAULT_WRITE_RETRY_BACKOFF_SECONDS = 0.1

T = TypeVar("T")


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


def _parse_json_value(value: str) -> Any:
    return json.loads(value)


def _parse_entity_provenance(value: str) -> list[dict[str, Any]]:
    parsed = _parse_json_value(value)
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return [parsed] if parsed else []
    message = "Expected provenance payload to be a JSON object or list"
    raise TypeError(message)


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
        self._busy_timeout_ms = DEFAULT_SQLITE_BUSY_TIMEOUT_MS
        self._max_write_retries = DEFAULT_WRITE_RETRY_COUNT
        self._write_retry_backoff = DEFAULT_WRITE_RETRY_BACKOFF_SECONDS
        self._initialize_sqlite_pragmas()

        self._provenance_tracker = ProvenanceTracker(self._connect)
        self._rdf_sync = RDFGraphSync()
        self._graph = self._rdf_sync.load_from_store(self)

    def get_rdf_graph(self) -> Graph:
        return self._graph

    def create_run(self, source_url: str, detected_type: str) -> str:
        run_id = str(uuid4())
        now = _utcnow_iso()
        def _write() -> None:
            with self._connect() as connection, connection:
                connection.execute(
                    """
                    INSERT INTO runs (
                        id,
                        source_url,
                        detected_type,
                        status,
                        stats,
                        started_at,
                        completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        run_id,
                        source_url,
                        detected_type,
                        "running",
                        json.dumps({}),
                        now,
                        None,
                    ),
                )

        self._run_write_with_retry(_write)
        return run_id

    def complete_run(self, run_id: str, stats: dict[str, Any]) -> bool:
        completed_at = _utcnow_iso()
        def _write() -> int:
            with self._connect() as connection, connection:
                cursor = connection.execute(
                    """
                    UPDATE runs
                    SET status = ?, stats = ?, completed_at = ?
                    WHERE id = ?;
                    """,
                    (
                        "completed",
                        json.dumps(stats),
                        completed_at,
                        run_id,
                    ),
                )
            return cursor.rowcount

        return self._run_write_with_retry(_write) > 0

    def fail_run(self, run_id: str, error_detail: str) -> bool:
        completed_at = _utcnow_iso()
        def _write() -> bool:
            with self._connect() as connection, connection:
                row = connection.execute(
                    "SELECT stats FROM runs WHERE id = ?;",
                    (run_id,),
                ).fetchone()
                if row is None:
                    return False

                stats = _parse_json_object(str(row["stats"]))
                stats["error_detail"] = error_detail
                cursor = connection.execute(
                    """
                    UPDATE runs
                    SET status = ?, stats = ?, completed_at = ?
                    WHERE id = ?;
                    """,
                    (
                        "failed",
                        json.dumps(stats),
                        completed_at,
                        run_id,
                    ),
                )
            return cursor.rowcount > 0

        return self._run_write_with_retry(_write)

    def get_run(self, run_id: str) -> Run | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    source_url,
                    detected_type,
                    status,
                    stats,
                    started_at,
                    completed_at
                FROM runs
                WHERE id = ?;
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    def get_runs_by_source(self, source_url: str, limit: int = 10) -> list[Run]:
        if limit <= 0:
            return []

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    source_url,
                    detected_type,
                    status,
                    stats,
                    started_at,
                    completed_at
                FROM runs
                WHERE source_url = ?
                ORDER BY started_at DESC, id DESC
                LIMIT ?;
                """,
                (source_url, limit),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def insert_entity(
        self,
        entity_type: str,
        entity_id: str,
        data: dict[str, Any],
        identifiers: dict[str, Any],
        id_source: str,
    ) -> str:
        now = _utcnow_iso()
        def _write() -> None:
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
                        json.dumps([]),
                        now,
                        now,
                    ),
                )

        self._run_write_with_retry(_write)

        self._rdf_sync.apply_entity_delta(
            graph=self._graph,
            entity_type=entity_type,
            entity_data=self._build_entity_rdf_payload(
                entity_type=entity_type,
                entity_id=entity_id,
                data=data,
                identifiers=identifiers,
            ),
            action="upsert",
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
        def _write() -> int:
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
            return cursor.rowcount

        updated = self._run_write_with_retry(_write) > 0
        if not updated:
            return False

        refreshed = self.get_entity(entity_id)
        if refreshed is not None:
            self._rdf_sync.apply_entity_delta(
                graph=self._graph,
                entity_type=refreshed.type,
                entity_data=self._build_entity_rdf_payload(
                    entity_type=refreshed.type,
                    entity_id=refreshed.id,
                    data=refreshed.data,
                    identifiers=refreshed.identifiers,
                ),
                action="upsert",
            )

        return True

    def upsert_entity(  # noqa: PLR0913
        self,
        entity_type: str,
        entity_id: str,
        data: dict[str, Any],
        identifiers: dict[str, Any],
        id_source: str,
        *,
        source: str = "unknown",
        run_id: str | None = None,
    ) -> MergeResult:
        existing = self.get_entity(entity_id)
        if existing is None:
            self.insert_entity(
                entity_type=entity_type,
                entity_id=entity_id,
                data=data,
                identifiers=identifiers,
                id_source=id_source,
            )
            return MergeResult(
                merged_data={
                    "id": entity_id,
                    "type": entity_type,
                    "data": data,
                    "identifiers": identifiers,
                    "id_source": id_source,
                },
                changed_fields=[],
                provenance_updates=[],
            )

        policy = MergePolicy(source=source, run_id=run_id)
        merge_result = policy.merge_entities(
            existing={
                "data": existing.data,
                "identifiers": existing.identifiers,
                "id_source": existing.id_source,
            },
            incoming={
                "data": data,
                "identifiers": identifiers,
                "id_source": id_source,
            },
            entity_type=entity_type,
        )

        merged_data = cast("dict[str, Any]", merge_result.merged_data.get("data", {}))
        merged_identifiers = cast(
            "dict[str, Any]",
            merge_result.merged_data.get("identifiers", {}),
        )
        merged_id_source = str(merge_result.merged_data.get("id_source", existing.id_source))

        if merge_result.changed_fields:
            now = _utcnow_iso()
            def _write() -> None:
                with self._connect() as connection, connection:
                    connection.execute(
                        """
                        UPDATE entities
                        SET data = ?, identifiers = ?, id_source = ?, last_seen = ?
                        WHERE id = ?;
                        """,
                        (
                            json.dumps(merged_data),
                            json.dumps(merged_identifiers),
                            merged_id_source,
                            now,
                            entity_id,
                        ),
                    )

            self._run_write_with_retry(_write)

            for update in merge_result.provenance_updates:
                self._provenance_tracker.record_change(
                    entity_id=entity_id,
                    field=str(update["field"]),
                    old_value=update.get("old_value"),
                    new_value=update.get("new_value"),
                    source=source,
                    run_id=run_id,
                )

            self._rdf_sync.apply_entity_delta(
                graph=self._graph,
                entity_type=entity_type,
                entity_data=self._build_entity_rdf_payload(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    data=merged_data,
                    identifiers=merged_identifiers,
                ),
                action="upsert",
            )

        return MergeResult(
            merged_data={
                "id": entity_id,
                "type": entity_type,
                "data": merged_data,
                "identifiers": merged_identifiers,
                "id_source": merged_id_source,
            },
            changed_fields=merge_result.changed_fields,
            provenance_updates=merge_result.provenance_updates,
        )

    def get_provenance(self, entity_id: str) -> list[ProvenanceEntry]:
        return self._provenance_tracker.get_provenance(entity_id)

    def get_provenance_for_field(
        self,
        entity_id: str,
        field: str,
    ) -> list[ProvenanceEntry]:
        return self._provenance_tracker.get_provenance_for_field(entity_id, field)

    def delete_entity(self, entity_id: str) -> bool:
        existing = self.get_entity(entity_id)
        def _write() -> int:
            with self._connect() as connection, connection:
                cursor = connection.execute(
                    "DELETE FROM entities WHERE id = ?;",
                    (entity_id,),
                )
            return cursor.rowcount

        deleted = self._run_write_with_retry(_write) > 0
        if deleted and existing is not None:
            self._rdf_sync.apply_entity_delta(
                graph=self._graph,
                entity_type=existing.type,
                entity_data={"id": existing.id},
                action="delete",
            )
        return deleted

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
        def _write() -> None:
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

        self._run_write_with_retry(_write)

        inserted = self.get_edge(edge_id)
        if inserted is not None:
            self._rdf_sync.apply_edge_delta(
                graph=self._graph,
                edge=inserted,
                action="upsert",
            )

        return edge_id

    def get_edge(self, edge_id: str) -> Edge | None:
        with self._connect() as connection:
            row = connection.execute(
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
                WHERE id = ?;
                """,
                (edge_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_edge(row)

    def get_all_edges(self) -> list[Edge]:
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
            ORDER BY created_at ASC, id ASC;
            """,
            (),
        )

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
        existing = self.get_edge(edge_id)
        def _write() -> int:
            with self._connect() as connection, connection:
                cursor = connection.execute(
                    "DELETE FROM edges WHERE id = ?;",
                    (edge_id,),
                )
            return cursor.rowcount

        deleted = self._run_write_with_retry(_write) > 0
        if deleted and existing is not None:
            self._rdf_sync.apply_edge_delta(
                graph=self._graph,
                edge=existing,
                action="delete",
            )
        return deleted

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

        def _write() -> str:
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

        return self._run_write_with_retry(_write)

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
        def _write() -> int:
            with self._connect() as connection, connection:
                cursor = connection.execute(
                    "DELETE FROM aliases WHERE id = ?;",
                    (alias_id,),
                )
            return cursor.rowcount

        return self._run_write_with_retry(_write) > 0

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self._db_path,
            timeout=self._busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms};")
        connection.execute("PRAGMA synchronous = NORMAL;")
        return connection

    def _initialize_sqlite_pragmas(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL;")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms};")
            connection.execute("PRAGMA synchronous = NORMAL;")

    def _run_write_with_retry(self, operation: Callable[[], T]) -> T:
        retrying_operation = with_write_retry(
            max_retries=self._max_write_retries,
            backoff_base=self._write_retry_backoff,
        )(operation)
        return retrying_operation()

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
    def _build_entity_rdf_payload(
        *,
        entity_type: str,
        entity_id: str,
        data: dict[str, Any],
        identifiers: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "id": entity_id,
            "type": entity_type,
            **data,
            "identifiers": identifiers,
        }

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> Entity:
        return Entity(
            id=str(row["id"]),
            type=str(row["type"]),
            data=_parse_json_object(str(row["data"])),
            identifiers=_parse_json_object(str(row["identifiers"])),
            id_source=str(row["id_source"]),
            provenance=_parse_entity_provenance(str(row["provenance"])),
            last_seen=_parse_timestamp(str(row["last_seen"])),
            created_at=_parse_timestamp(str(row["created_at"])),
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> Run:
        stats = _parse_json_object(str(row["stats"]))
        completed_at_raw = row["completed_at"]
        completed_at = (
            _parse_timestamp(str(completed_at_raw))
            if completed_at_raw is not None
            else None
        )
        error_detail = stats.get("error_detail")
        if error_detail is not None:
            error_detail = str(error_detail)

        return Run(
            id=str(row["id"]),
            source_url=str(row["source_url"]),
            detected_type=str(row["detected_type"]),
            status=str(row["status"]),
            stats=stats,
            started_at=_parse_timestamp(str(row["started_at"])),
            completed_at=completed_at,
            error_detail=error_detail,
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
