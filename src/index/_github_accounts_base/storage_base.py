"""DuckDB storage helpers shared by the github user/org indices.

These are plain functions (no class hierarchy). Each concrete store
owns its own DuckDB connection and its own `upsert_<kind>` method —
the helpers here cover the parts that don't depend on the record
shape: schema bootstrap, generic count/fetch, the chunks table
contract, and the `stream_unembedded` query (table-name parameterised).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import duckdb

LOGGER = logging.getLogger(__name__)


def bootstrap_schema(conn: duckdb.DuckDBPyConnection, schema_path: Path) -> None:
    """Apply the schema SQL idempotently. The file must use IF NOT EXISTS
    on every statement so re-runs are safe."""
    conn.execute(schema_path.read_text(encoding="utf-8"))


def count_table(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    """`SELECT count(*) FROM <table>` — caller is responsible for
    passing a trusted table name (we don't accept user input here)."""
    result = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    return int(result[0]) if result else 0


def fetch_one(
    conn: duckdb.DuckDBPyConnection,
    *,
    table: str,
    id_column: str,
    id_value: str,
) -> dict[str, Any] | None:
    """`SELECT * FROM <table> WHERE <id_column> = ?` — column dict or None."""
    cur = conn.execute(
        f"SELECT * FROM {table} WHERE {id_column} = ?",
        [id_value],
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row, strict=False))


def stream_unembedded(
    conn: duckdb.DuckDBPyConnection,
    *,
    table: str,
    id_column: str,
    entity_type: str,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield rows that have no matching `chunks` entry yet.

    The join key is `chunks.entity_type = ? AND chunks.entity_id = t.<id_column>`,
    so calling code controls both the source table and the `entity_type`
    string. Same shape as the existing repo index's
    `stream_rows_for_embedding`.
    """
    sql = (
        f"SELECT t.* FROM {table} t "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM chunks c "
        f"  WHERE c.entity_type = ? AND c.entity_id = t.{id_column}"
        ")"
    )
    params: list[Any] = [entity_type]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    for row in rows:
        yield dict(zip(cols, row, strict=False))


def upsert_chunk(
    conn: duckdb.DuckDBPyConnection,
    *,
    chunk_id: str,
    entity_type: str,
    entity_id: str,
    chunk_index: int,
    text: str,
    token_count: int,
    vector_id: str,
) -> None:
    """Idempotent insert into the shared `chunks` table contract.

    Schema is identical across all account indices — primary key is
    `chunk_id` (deterministic uuid5 of entity_type|entity_id|index), so
    re-running an embed cleanly overwrites the prior point.
    """
    conn.execute(
        "INSERT INTO chunks "
        "(chunk_id, entity_type, entity_id, chunk_index, text, "
        "token_count, vector_id) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (chunk_id) DO UPDATE SET "
        "text = excluded.text, token_count = excluded.token_count, "
        "vector_id = excluded.vector_id, embedded_at = now()",
        [chunk_id, entity_type, entity_id, chunk_index, text, token_count, vector_id],
    )
