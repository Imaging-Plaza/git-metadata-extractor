from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from src.v2.graph.models import ProvenanceEntry

if TYPE_CHECKING:
    import sqlite3


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _parse_provenance_payload(value: str) -> list[dict[str, Any]]:
    payload = json.loads(value)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload] if payload else []
    message = "Entity provenance payload must be a JSON object or list"
    raise TypeError(message)


class ProvenanceTracker:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def record_change(  # noqa: PLR0913
        self,
        entity_id: str,
        field: str,
        old_value: Any,
        new_value: Any,
        source: str,
        run_id: str | None,
    ) -> ProvenanceEntry:
        entry = ProvenanceEntry(
            entity_id=entity_id,
            field=field,
            old_value=old_value,
            new_value=new_value,
            source=source,
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
        )
        serialized_entry = {
            "entity_id": entry.entity_id,
            "field": entry.field,
            "old_value": entry.old_value,
            "new_value": entry.new_value,
            "source": entry.source,
            "run_id": entry.run_id,
            "timestamp": entry.timestamp.isoformat(),
        }

        with self._connect() as connection, connection:
            row = connection.execute(
                "SELECT provenance FROM entities WHERE id = ?;",
                (entity_id,),
            ).fetchone()
            if row is None:
                message = f"Entity '{entity_id}' does not exist"
                raise ValueError(message)

            provenance_payload = _parse_provenance_payload(str(row["provenance"]))
            provenance_payload.append(serialized_entry)
            connection.execute(
                """
                UPDATE entities
                SET provenance = ?, last_seen = ?
                WHERE id = ?;
                """,
                (
                    json.dumps(provenance_payload),
                    entry.timestamp.isoformat(),
                    entity_id,
                ),
            )
        return entry

    def get_provenance(self, entity_id: str) -> list[ProvenanceEntry]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT provenance FROM entities WHERE id = ?;",
                (entity_id,),
            ).fetchone()
        if row is None:
            return []

        payload = _parse_provenance_payload(str(row["provenance"]))
        entries = [
            ProvenanceEntry(
                entity_id=str(item.get("entity_id", entity_id)),
                field=str(item["field"]),
                old_value=item.get("old_value"),
                new_value=item.get("new_value"),
                source=str(item.get("source", "unknown")),
                run_id=(
                    str(item["run_id"])
                    if item.get("run_id") is not None
                    else None
                ),
                timestamp=_parse_timestamp(str(item["timestamp"])),
            )
            for item in payload
            if "field" in item and "timestamp" in item
        ]
        return sorted(entries, key=lambda entry: entry.timestamp)

    def get_provenance_for_field(
        self,
        entity_id: str,
        field: str,
    ) -> list[ProvenanceEntry]:
        entries = self.get_provenance(entity_id)
        return [entry for entry in entries if entry.field == field]
