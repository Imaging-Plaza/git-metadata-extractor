"""Async ingest helper for the dockerhub index, called from `/v2/indices/dockerhub/ingest`.

Each item in the request body is a Docker Hub image reference dispatched to
:func:`src.index.dockerhub.ingest.repos.ingest_single_image` against a shared
``DockerhubStore`` + ``DockerHubClient`` cached on ``app.state``. A failure on
one image does not stop the rest; per-image outcomes land on the job summary.
After ingest, the embed step is chained (DuckDB -> RCP -> Qdrant).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    DockerhubIngestRequest,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
)
from src.v2.indices._embed_step import run_embed_step
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "dockerhub"


def get_or_create_dockerhub_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, store, client) on ``app.state``."""

    cached = getattr(app_state, "v2_dockerhub_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.dockerhub.config import load_config  # noqa: PLC0415
        from src.index.dockerhub.ingest.dockerhub_client import DockerHubClient  # noqa: PLC0415
        from src.index.dockerhub.storage.duckdb_store import DockerhubStore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("dockerhub ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        store = DockerhubStore.open(config.paths.duckdb_path)
        client = DockerHubClient(
            api_base=config.dockerhub.api_base,
            token=config.dockerhub.token,
            cache_path=config.paths.cache_db_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dockerhub ingest: resource init failed — %s", exc)
        return None
    app_state.v2_dockerhub_resources = (config, store, client)
    return app_state.v2_dockerhub_resources


def _ingest_one_image(
    image_ref: str, *, config: Any, store: Any, client: Any,
) -> dict[str, Any]:
    """Run a single per-image ingest; never raises."""
    try:
        from src.index.dockerhub.ingest.repos import (  # noqa: PLC0415
            ingest_single_image,
        )
        outcome = ingest_single_image(
            config=config, store=store, client=client, image_ref=image_ref,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dockerhub ingest: %s failed — %s", image_ref, exc)
        return {"image": image_ref, "outcome": "failed", "error": str(exc)}
    return {"image": image_ref, "outcome": outcome}


async def run_dockerhub_ingest_job(
    *,
    payload: DockerhubIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each image and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_dockerhub_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "dockerhub index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, store, client = resources

        items_results: list[dict[str, Any]] = []
        for image_ref in payload.images:
            result = await asyncio.to_thread(
                _ingest_one_image,
                image_ref, config=config, store=store, client=client,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
        ingested = sum(1 for r in items_results if r["outcome"] == "ingested")
        skipped_404 = sum(1 for r in items_results if r["outcome"] == "skipped_404")
        failed = sum(1 for r in items_results if r["outcome"] == "failed")

        from src.index.dockerhub.embed.pipeline import embed_images  # noqa: PLC0415
        embed_summary = await run_embed_step(
            provider=INDEX_NAME,
            job_id=job_id,
            embed_call=lambda: embed_images(config=config, store=store),
            checkpoint_store=store,
        )

        finished.status = IndexIngestJobStatus.COMPLETED
        finished.summary = {
            "requested": len(payload.images),
            "ingested": ingested,
            "skipped_404": skipped_404,
            "failed": failed,
            "items": items_results,
            "embed": embed_summary,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("dockerhub ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_dockerhub_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against the Docker Hub index."""
    resources = get_or_create_dockerhub_resources(app_state)
    if resources is None:
        return None
    config, store, _ = resources
    from src.index.dockerhub.retrieval.semantic import semantic_search  # noqa: PLC0415
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
    "get_or_create_dockerhub_resources",
    "run_dockerhub_ingest_job",
    "run_dockerhub_search",
]
