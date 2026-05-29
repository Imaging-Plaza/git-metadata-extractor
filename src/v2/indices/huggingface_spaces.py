"""Async ingest helper for the huggingface_spaces index."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    HuggingFaceSpacesIngestRequest,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
)
from src.v2.indices._embed_step import run_embed_step
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "huggingface_spaces"


def get_or_create_huggingface_spaces_resources(app_state: Any) -> Any | None:
    cached = getattr(app_state, "v2_huggingface_spaces_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index._huggingface_base.client import HFClient  # noqa: PLC0415
        from src.index.huggingface_spaces.config import load_config  # noqa: PLC0415
        from src.index.huggingface_spaces.storage.duckdb_store import (  # noqa: PLC0415
            HuggingFaceSpacesStore,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("huggingface_spaces ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        store = HuggingFaceSpacesStore.open(config.paths.duckdb_path)
        client = HFClient(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("huggingface_spaces ingest: resource init failed — %s", exc)
        return None
    app_state.v2_huggingface_spaces_resources = (config, store, client)
    return app_state.v2_huggingface_spaces_resources


def _ingest_one(repo_id: str, *, config: Any, store: Any, client: Any) -> dict[str, Any]:
    try:
        from src.index.huggingface_spaces.ingest.spaces import (  # noqa: PLC0415
            ingest_single_space,
        )
        outcome = ingest_single_space(
            config=config, store=store, client=client, repo_id=repo_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("huggingface_spaces ingest: %s failed — %s", repo_id, exc)
        return {"repo_id": repo_id, "outcome": "failed", "error": str(exc)}
    return {"repo_id": repo_id, "outcome": outcome}


async def run_huggingface_spaces_ingest_job(
    *,
    payload: HuggingFaceSpacesIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_huggingface_spaces_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "huggingface_spaces index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, store, client = resources

        items_results: list[dict[str, Any]] = []
        for repo_id in payload.repo_ids:
            result = await asyncio.to_thread(
                _ingest_one, repo_id, config=config, store=store, client=client,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
        ingested = sum(1 for r in items_results if r["outcome"] == "ingested")
        skipped_404 = sum(1 for r in items_results if r["outcome"] == "skipped_404")
        failed = sum(1 for r in items_results if r["outcome"] == "failed")

        from src.index.huggingface_spaces.embed.pipeline import embed_spaces  # noqa: PLC0415
        embed_summary = await run_embed_step(
            provider=INDEX_NAME,
            job_id=job_id,
            embed_call=lambda: embed_spaces(config=config, store=store),
            checkpoint_store=store,
        )

        finished.status = IndexIngestJobStatus.COMPLETED
        finished.summary = {
            "requested": len(payload.repo_ids),
            "ingested": ingested,
            "skipped_404": skipped_404,
            "failed": failed,
            "items": items_results,
            "embed": embed_summary,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("huggingface_spaces ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_huggingface_spaces_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    resources = get_or_create_huggingface_spaces_resources(app_state)
    if resources is None:
        return None
    config, store, _ = resources
    from src.index.huggingface_spaces.retrieval.semantic import (  # noqa: PLC0415
        semantic_search,
    )
    raw_hits = await asyncio.to_thread(
        semantic_search,
        config=config, query=payload.query,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k or max(payload.top_k * 5, 50),
        filter_payload=payload.filter_payload,
        store=store,
    )
    return IndexSearchResponse(
        index_name=INDEX_NAME,
        target=None,
        query=payload.query,
        hits=[hit_from_raw(h) for h in raw_hits],
    )


__all__ = [
    "INDEX_NAME",
    "get_or_create_huggingface_spaces_resources",
    "run_huggingface_spaces_ingest_job",
    "run_huggingface_spaces_search",
]
