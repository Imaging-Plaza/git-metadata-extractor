"""Async ingest helper for the huggingface_papers index.

Mirrors src/v2/indices/github_users.py — each request item is
normalised, fetched via the dedicated HF Papers REST client, and
persisted to the huggingface_papers DuckDB + Qdrant collection.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    HuggingFacePapersIngestRequest,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
)
from src.v2.indices._embed_step import run_embed_step
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "huggingface_papers"


def get_or_create_huggingface_papers_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, store, client) on ``app.state``."""

    cached = getattr(app_state, "v2_huggingface_papers_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.huggingface_papers.config import load_config  # noqa: PLC0415
        from src.index.huggingface_papers.ingest.hf_papers_client import (  # noqa: PLC0415
            HFPapersClient,
        )
        from src.index.huggingface_papers.storage.duckdb_store import (  # noqa: PLC0415
            HuggingFacePapersStore,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "huggingface_papers ingest: index module unavailable — %s", exc,
        )
        return None
    try:
        config = load_config()
        store = HuggingFacePapersStore.open(config.paths.duckdb_path)
        client = HFPapersClient(
            api_base=config.huggingface.api_base,
            token=config.huggingface.token,
            cache_path=config.paths.cache_db_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("huggingface_papers ingest: resource init failed — %s", exc)
        return None
    app_state.v2_huggingface_papers_resources = (config, store, client)
    return app_state.v2_huggingface_papers_resources


def _ingest_one_paper(
    raw_arxiv_id: str, *, config: Any, store: Any, client: Any,
) -> dict[str, Any]:
    """Per-paper ingest; never raises. Normalises wire input before
    calling the underlying ``ingest_single_paper``."""
    try:
        from src.index.huggingface_papers.ingest.hf_papers_client import (  # noqa: PLC0415
            normalize_arxiv_id,
        )
        from src.index.huggingface_papers.ingest.papers import (  # noqa: PLC0415
            ingest_single_paper,
        )
        arxiv_id = normalize_arxiv_id(raw_arxiv_id)
        if arxiv_id is None:
            return {
                "arxiv_id": raw_arxiv_id,
                "outcome": "failed",
                "error": "malformed arxiv id",
            }
        outcome = ingest_single_paper(
            config=config, store=store, client=client, arxiv_id=arxiv_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "huggingface_papers ingest: %s failed — %s", raw_arxiv_id, exc,
        )
        return {"arxiv_id": raw_arxiv_id, "outcome": "failed", "error": str(exc)}
    return {"arxiv_id": raw_arxiv_id, "outcome": outcome}


async def run_huggingface_papers_ingest_job(
    *,
    payload: HuggingFacePapersIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each paper and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_huggingface_papers_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = (
                "huggingface_papers index module unavailable on this deployment"
            )
            job_store.set(existing)
            return
        config, store, client = resources

        items_results: list[dict[str, Any]] = []
        for raw_arxiv_id in payload.arxiv_ids:
            result = await asyncio.to_thread(
                _ingest_one_paper,
                raw_arxiv_id, config=config, store=store, client=client,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
        ingested = sum(1 for r in items_results if r["outcome"] == "ingested")
        skipped_404 = sum(1 for r in items_results if r["outcome"] == "skipped_404")
        failed = sum(1 for r in items_results if r["outcome"] == "failed")

        from src.index.huggingface_papers.embed.pipeline import embed_papers  # noqa: PLC0415
        embed_summary = await run_embed_step(
            provider=INDEX_NAME,
            job_id=job_id,
            embed_call=lambda: embed_papers(config=config, store=store),
        )

        finished.status = IndexIngestJobStatus.COMPLETED
        finished.summary = {
            "requested": len(payload.arxiv_ids),
            "ingested": ingested,
            "skipped_404": skipped_404,
            "failed": failed,
            "items": items_results,
            "embed": embed_summary,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("huggingface_papers ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_huggingface_papers_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against the huggingface_papers index."""
    resources = get_or_create_huggingface_papers_resources(app_state)
    if resources is None:
        return None
    config, store, _ = resources
    from src.index.huggingface_papers.retrieval.semantic import (  # noqa: PLC0415
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
    "get_or_create_huggingface_papers_resources",
    "run_huggingface_papers_ingest_job",
    "run_huggingface_papers_search",
]
