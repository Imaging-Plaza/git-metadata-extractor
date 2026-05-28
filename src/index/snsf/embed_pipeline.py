"""Embed-and-upsert pipeline for the SNSF P3 index (Phase 2).

Streams scope-membership grant rows from DuckDB → builds the embedding
text via `document.to_document` → embeds via RCP `Qwen3-Embedding-8B` →
upserts to Qdrant `snsf_<scope>`.

Handles the EPFL slice (~6 188 grants) in a couple of minutes. The
worldwide scope (~90 k) is ~30 min on RCP.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, Dict, List, Optional

from src.index.snsf.config import SnsfIndexConfig
from src.index.snsf.document import to_document
from src.index.snsf.embed import embed_passages
from src.index.snsf.qdrant_store import QdrantSnsfStore
from src.index.snsf.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)


def _payload(grant: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Qdrant payload — small subset of grant columns useful for filtering / rendering."""
    return {
        "grant_number":          grant.get("grant_number"),
        "title":                 grant.get("title_english") or grant.get("title"),
        "research_institution":  grant.get("research_institution"),
        "research_institution_type": grant.get("research_institution_type"),
        "main_discipline":       grant.get("main_discipline"),
        "main_discipline_l1":    grant.get("main_discipline_l1"),
        "main_discipline_l2":    grant.get("main_discipline_l2"),
        "main_field_of_research": grant.get("main_field_of_research"),
        "start_date":            _iso(grant.get("start_date")),
        "end_date":              _iso(grant.get("end_date")),
        "amount_granted":        grant.get("amount_granted"),
        "state":                 grant.get("state"),
        "funding_instrument":    grant.get("funding_instrument"),
        "responsible_applicant": grant.get("responsible_applicant"),
        "text":                  text,
    }


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return str(v)


def _scope_grant_rows(store: DuckDBStore, scope_mode: str) -> List[Dict[str, Any]]:
    """Return all grant rows for `scope_mode`, ordered by grant_number desc."""
    cur = store.connect().execute(
        """
        SELECT g.* FROM grants g
        JOIN scope_records s ON s.grant_number = g.grant_number
        WHERE s.scope_mode = ?
        ORDER BY g.grant_number DESC
        """,
        [scope_mode],
    )
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=False)) for row in rows]


async def run(
    cfg: SnsfIndexConfig,
    *,
    scope_mode: Optional[str] = None,
    recreate: bool = True,
) -> Dict[str, Any]:
    """Embed all grants in the active scope and upsert to Qdrant."""
    if cfg.rcp.token is None:
        msg = (
            "RCP_TOKEN is not set. Required for SNSF embedding. "
            "Source the project .env (`set -a; source .env; set +a`) and retry."
        )
        raise RuntimeError(msg)

    active = scope_mode or cfg.scope.active
    LOGGER.info("Starting SNSF embed pipeline for scope=%s", active)

    store = DuckDBStore.open()
    try:
        rows = _scope_grant_rows(store, active)
    finally:
        store.close()
    LOGGER.info("Loaded %d grants for scope=%s from DuckDB", len(rows), active)

    if not rows:
        msg = (
            f"No grants in scope_records for scope={active!r}. "
            f"Run `python -m src.index.snsf load-local --scope {active}` first."
        )
        raise RuntimeError(msg)

    texts = [to_document(r) for r in rows]
    nonempty_ratio = sum(1 for t in texts if t) / len(texts)
    LOGGER.info(
        "Built embedding text for %d grants (non-empty ratio: %.1f%%)",
        len(texts), 100 * nonempty_ratio,
    )

    LOGGER.info(
        "Embedding via RCP %s (batch_size=%d, max_concurrency=%d)",
        cfg.rcp.embedding_model, cfg.rcp.batch_size, cfg.rcp.max_concurrency,
    )
    matrix = await embed_passages(cfg.rcp, texts, normalize=True)
    LOGGER.info("Embedded matrix shape=%s", matrix.shape)

    qstore = QdrantSnsfStore(cfg)
    if recreate:
        qstore.recreate_collection(active)
    else:
        qstore.ensure_collection(active)

    grant_numbers = [int(r["grant_number"]) for r in rows]
    payloads = [_payload(r, t) for r, t in zip(rows, texts)]
    qstore.upsert_records(
        active,
        grant_numbers=grant_numbers,
        vectors=matrix.tolist(),
        payloads=payloads,
    )
    qcount = qstore.count(active)

    return {
        "scope_mode": active,
        "grants_embedded": len(rows),
        "qdrant_count": qcount,
        "qdrant_collection": qstore.collection_name(active),
        "embedding_model": cfg.rcp.embedding_model,
    }


def run_sync(cfg: SnsfIndexConfig, **kwargs: Any) -> Dict[str, Any]:
    return asyncio.run(run(cfg, **kwargs))


__all__ = ["run", "run_sync"]
