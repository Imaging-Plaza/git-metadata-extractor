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

WAL checkpoint toggle
---------------------
The per-provider stores keep one long-lived DuckDB writer connection
cached on ``app.state`` and never close it between requests, so writes
sit in the ``-wal`` sidecar until DuckDB checkpoints (on clean
connection close, or when the WAL grows past its auto-threshold).
DuckDB *does* replay the WAL on the next open, so this is not data
loss — but until a checkpoint lands, a read-only probe from another
process sees the main file without the in-flight rows, and a hard
crash leaves a fat WAL to replay.

``run_embed_step`` therefore issues a single ``CHECKPOINT`` after each
job's embed step (success or failure — the ingest writes committed
either way), folding the main file forward. It is gated by the
``INDEX_DUCKDB_CHECKPOINT`` env var (default on); set it to a falsey
value (``0`` / ``false`` / ``no`` / ``off``) to skip the checkpoint and
keep the pre-existing WAL-deferred behaviour. The cost is one
checkpoint per job, not per batch, so it is negligible next to the
embed round-trips it follows.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

_TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
_FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}

_CHECKPOINT_ENV_VAR = "INDEX_DUCKDB_CHECKPOINT"


def _checkpoint_enabled() -> bool:
    """Read the WAL-checkpoint toggle. Defaults on; explicit falsey disables.

    Anything unrecognised falls back to the default (on) so a typo never
    silently turns durability off.
    """
    raw = os.getenv(_CHECKPOINT_ENV_VAR)
    if raw is None:
        return True
    value = raw.strip().lower()
    if value in _FALSE_ENV_VALUES:
        return False
    if value in _TRUE_ENV_VALUES:
        return True
    LOGGER.warning(
        "%s=%r not recognised; defaulting to enabled", _CHECKPOINT_ENV_VAR, raw,
    )
    return True


def _checkpoint_sync(store: Any) -> None:
    """Force the store's WAL into its main DuckDB file. Runs on the cached
    writer connection so it sees every committed write from this job."""
    store.connect().execute("CHECKPOINT")


async def _maybe_checkpoint(
    *, provider: str, job_id: str, store: Any | None,
) -> dict[str, Any]:
    """Optionally CHECKPOINT the store's DuckDB. Never raises.

    Returns a small status block for the job summary. A checkpoint
    failure (e.g. another writer holds the file) is logged and recorded
    but does not propagate — the WAL still replays on next open.
    """
    if not _checkpoint_enabled():
        return {"enabled": False}
    if store is None or not hasattr(store, "connect"):
        return {"enabled": True, "ran": False, "reason": "no store connection"}
    try:
        await asyncio.to_thread(_checkpoint_sync, store)
        return {"enabled": True, "ran": True, "ok": True}
    except Exception as exc:  # noqa: BLE001 — checkpoint is best-effort
        LOGGER.warning(
            "%s checkpoint failed (job=%s): %s — WAL will replay on next open",
            provider, job_id, exc,
        )
        return {"enabled": True, "ran": True, "ok": False, "error": str(exc)}


async def run_embed_step(
    *,
    provider: str,
    job_id: str,
    embed_call: Callable[[], dict[str, Any]],
    checkpoint_store: Any | None = None,
) -> dict[str, Any]:
    """Run a sync embed callable in a worker thread; return a summary block.

    The closure is dispatched via ``asyncio.to_thread`` so blocking I/O
    (HTTP to RCP, Qdrant upserts, DuckDB reads) doesn't block the
    event loop. Exceptions are caught, logged, and surfaced in the
    returned dict — never re-raised, since the ingest half has
    already committed and the operator just needs to know the embed
    half did not.

    When ``checkpoint_store`` is supplied and the
    ``INDEX_DUCKDB_CHECKPOINT`` toggle is on (default), a single
    ``CHECKPOINT`` is issued afterward to fold that store's WAL into its
    main file. It runs regardless of embed outcome — the upstream-fetch
    writes committed before embed began — and its status lands under the
    returned ``"checkpoint"`` key.
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

    out["checkpoint"] = await _maybe_checkpoint(
        provider=provider, job_id=job_id, store=checkpoint_store,
    )

    finished = datetime.now(timezone.utc)
    out["completed_at"] = finished.isoformat()
    out["duration_seconds"] = (finished - started).total_seconds()
    return out


__all__ = ["run_embed_step"]
