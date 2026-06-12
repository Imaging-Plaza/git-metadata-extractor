"""Generic ingest-job + search runners for the GitLab index family.

Backs `POST /v2/indices/gitlab_*/ingest` and `.../search` for all nine
gitlab stores (`gitlab_{epfl,ethz,datascience}_{projects,groups,users}`).

The leaf entrypoints (`src.index.<name>.ingest.run_ingest`,
`.embed.run_embed`, `.retrieval.search`) are uniform across the nine stores
and synchronous, so they are dispatched via :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    GitLabIngestRequest,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
)
from src.v2.indices._ingest_pool import run_in_ingest_pool
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

GITLAB_INDEX_NAMES: list[str] = [
    f"gitlab_{instance}_{entity}"
    for instance in ("epfl", "ethz", "datascience")
    for entity in ("projects", "groups", "users")
]


async def run_gitlab_ingest_job(
    *,
    index_name: str,
    payload: GitLabIngestRequest,
    app_state: Any,  # noqa: ARG001 — parity with sibling runners; gitlab leaves self-config
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: full-instance crawl + embed for a single gitlab store."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        if index_name not in GITLAB_INDEX_NAMES:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = f"unknown gitlab index: {index_name}"
            job_store.set(existing)
            return

        try:
            ingest_mod = importlib.import_module(f"src.index.{index_name}.ingest")
            embed_mod = importlib.import_module(f"src.index.{index_name}.embed")
        except Exception as exc:  # noqa: BLE001 — optional dependency
            logger.warning(
                "gitlab ingest: %s module unavailable — %s", index_name, exc,
            )
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "gitlab index module unavailable"
            job_store.set(existing)
            return

        ingested = await run_in_ingest_pool(ingest_mod.run_ingest, limit=payload.limit)
        embedded = await run_in_ingest_pool(embed_mod.run_embed)

        finished = job_store.get(job_id) or existing
        finished.status = IndexIngestJobStatus.COMPLETED
        finished.completed_at = datetime.now(timezone.utc)
        finished.summary = {
            "index": index_name,
            "ingested": ingested,
            "embedded": embedded,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("gitlab ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_gitlab_search(
    index_name: str,
    payload: IndexSearchRequest,
    app_state: Any,  # noqa: ARG001 — parity with sibling runners; gitlab leaves self-config
) -> IndexSearchResponse | None:
    """Run a semantic search against a single gitlab store.

    Returns ``None`` when the index name is unknown or the leaf module is
    unavailable on this deployment (caller maps this to 503).
    """
    if index_name not in GITLAB_INDEX_NAMES:
        return None
    try:
        retrieval_mod = importlib.import_module(
            f"src.index.{index_name}.retrieval",
        )
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning(
            "gitlab search: %s module unavailable — %s", index_name, exc,
        )
        return None
    raw_hits = await asyncio.to_thread(
        retrieval_mod.search,
        payload.query,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k or max(payload.top_k * 5, 50),
        filter_payload=payload.filter_payload,
    )
    return IndexSearchResponse(
        index_name=index_name,
        target=payload.target,
        query=payload.query,
        hits=[hit_from_raw(h) for h in raw_hits],
    )


__all__ = [
    "GITLAB_INDEX_NAMES",
    "run_gitlab_ingest_job",
    "run_gitlab_search",
]
