"""Extract `referenced_works` from `works.raw` into the normalised
`work_references` table.

This is the **API-free** path — it operates entirely on data already
present in DuckDB. For works whose ``raw`` pre-dates
``WORKS_PROJECTION`` containing ``referenced_works`` (i.e. ingested
before that field was added), use the discover/hydrate pipeline:

    gme discover --source from-references --indices openalex --out missing.jsonl
    gme hydrate missing.jsonl --indices openalex
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.index.openalex.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)

BATCH = 5000


def extract_from_raw(store: DuckDBStore) -> dict[str, int]:
    """Walk every row in ``works`` whose ``raw.referenced_works`` is populated,
    and bulk-insert the edges into ``work_references``.

    Idempotent: ``INSERT OR IGNORE`` on the composite primary key.
    Returns counts: ``{works_seen, works_with_refs, edges_inserted, final_edges}``.
    """
    cur = store.connect()

    n_existing = cur.execute("SELECT COUNT(*) FROM work_references").fetchone()[0]
    LOGGER.info("existing work_references rows: %d", n_existing)

    offset = 0
    works_seen = 0
    works_with_refs = 0
    edges_submitted = 0

    while True:
        rows = cur.execute(
            "SELECT openalex_id, raw FROM works WHERE raw IS NOT NULL "
            "ORDER BY openalex_id LIMIT ? OFFSET ?",
            [BATCH, offset],
        ).fetchall()
        if not rows:
            break
        batch_inserts: list[tuple[str, str, int]] = []
        for citing_id, raw in rows:
            works_seen += 1
            try:
                obj = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            refs = obj.get("referenced_works") or []
            if refs:
                works_with_refs += 1
            for pos, cited in enumerate(refs):
                if cited:
                    batch_inserts.append((citing_id, cited, pos))
        if batch_inserts:
            cur.executemany(
                "INSERT OR IGNORE INTO work_references "
                "(citing_work_id, cited_work_id, position) VALUES (?, ?, ?)",
                batch_inserts,
            )
            edges_submitted += len(batch_inserts)
        offset += BATCH
        LOGGER.info(
            "processed %d works (%d with refs, %d edges submitted)",
            works_seen, works_with_refs, edges_submitted,
        )

    final = cur.execute("SELECT COUNT(*) FROM work_references").fetchone()[0]
    return {
        "works_seen": works_seen,
        "works_with_refs": works_with_refs,
        "edges_submitted": edges_submitted,
        "edges_added": final - n_existing,
        "final_edges": final,
    }


__all__ = ["extract_from_raw"]
