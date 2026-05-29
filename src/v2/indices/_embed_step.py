"""Shared embed-after-ingest helper for the v2 ingest job runners.

Every provider's ingest job has two halves: a wire→DuckDB step
(the original ``_ingest_one_*`` loop) and a DuckDB→embed→Qdrant
step (the per-provider ``embed_*`` function). Historically only
the first half was chained into the API job runners; the second
had to be triggered out-of-band via CLI.

``run_embed_step`` lets each runner add the second half with one
``await`` and one closure, while keeping the embed failure mode
visible (recorded in the job ``summary['embed']`` block) and
non-fatal (an embed failure does not flip the ingest job to
``failed`` — the DuckDB writes already succeeded).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)


async def run_embed_step(
    *,
    provider: str,
    job_id: str,
    embed_call: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Run a sync embed callable in a worker thread; return a summary block.

    The closure is dispatched via ``asyncio.to_thread`` so blocking I/O
    (HTTP to RCP, Qdrant upserts, DuckDB reads) doesn't block the
    event loop. Exceptions are caught, logged, and surfaced in the
    returned dict — never re-raised, since the ingest half has
    already committed and the operator just needs to know the embed
    half did not.
    """
    started = datetime.now(timezone.utc)
    out: dict[str, Any] = {"started_at": started.isoformat()}
    try:
        result = await asyncio.to_thread(embed_call)
        out["ok"] = True
        out["result"] = result
    except Exception as exc:  # noqa: BLE001 — embed failures must not crash ingest job
        LOGGER.exception("%s embed step failed: job=%s", provider, job_id)
        out["ok"] = False
        out["error"] = str(exc)
    finished = datetime.now(timezone.utc)
    out["completed_at"] = finished.isoformat()
    out["duration_seconds"] = (finished - started).total_seconds()
    return out


__all__ = ["run_embed_step"]
