from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.v2.models import IntermediateEnvelope

if TYPE_CHECKING:
    from src.v2.graph.store import GraphStore


def _resolve_store_db_path(store: GraphStore) -> Path:
    raw_path = getattr(store, "_db_path", None)
    if not isinstance(raw_path, Path):
        message = "GraphStore does not expose a valid database path"
        raise TypeError(message)
    return raw_path


def assemble_intermediates(
    source_url: str | None,
    store: GraphStore,
    limit: int | None = None,
) -> list[IntermediateEnvelope]:
    if limit is not None and limit <= 0:
        return []

    db_path = _resolve_store_db_path(store)
    query = """
        SELECT
            agent_name,
            run_id,
            data,
            created_at
        FROM intermediates
        WHERE (? IS NULL OR source_url = ?)
        ORDER BY created_at DESC, id DESC
    """
    parameters: tuple[Any, ...] = (source_url, source_url)
    if limit is not None:
        query = f"{query} LIMIT ?;"
        parameters = (*parameters, limit)
    else:
        query = f"{query};"

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, parameters).fetchall()

    envelopes: list[IntermediateEnvelope] = []
    for row in rows:
        payload = json.loads(str(row["data"]))
        if not isinstance(payload, dict):
            payload = {"value": payload}
        envelopes.append(
            IntermediateEnvelope(
                agent_name=str(row["agent_name"]),
                run_id=str(row["run_id"]) if row["run_id"] is not None else None,
                timestamp=str(row["created_at"]),
                data=payload,
            ),
        )

    return envelopes
