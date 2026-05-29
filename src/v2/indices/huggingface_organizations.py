"""Async ingest helper for the huggingface_organizations index."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    HuggingFaceOrganizationsIngestRequest,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
)
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "huggingface_organizations"


def get_or_create_huggingface_organizations_resources(app_state: Any) -> Any | None:
    cached = getattr(app_state, "v2_huggingface_organizations_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index._huggingface_base.client import HFClient  # noqa: PLC0415
        from src.index.huggingface_organizations.config import (  # noqa: PLC0415
            load_config,
        )
        from src.index.huggingface_organizations.storage.duckdb_store import (  # noqa: PLC0415
            HuggingFaceOrganizationsStore,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "huggingface_organizations ingest: index module unavailable — %s", exc,
        )
        return None
    try:
        config = load_config()
        store = HuggingFaceOrganizationsStore.open(config.paths.duckdb_path)
        client = HFClient(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "huggingface_organizations ingest: resource init failed — %s", exc,
        )
        return None
    app_state.v2_huggingface_organizations_resources = (config, store, client)
    return app_state.v2_huggingface_organizations_resources


def _ingest_one(slug: str, *, config: Any, store: Any, client: Any) -> dict[str, Any]:
    try:
        from src.index.huggingface_organizations.ingest.organizations import (  # noqa: PLC0415
            ingest_single_organization,
        )
        outcome = ingest_single_organization(
            config=config, store=store, client=client, slug=slug,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("huggingface_organizations ingest: %s failed — %s", slug, exc)
        return {"slug": slug, "outcome": "failed", "error": str(exc)}
    return {"slug": slug, "outcome": outcome}


async def run_huggingface_organizations_ingest_job(
    *,
    payload: HuggingFaceOrganizationsIngestRequest,
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

        resources = get_or_create_huggingface_organizations_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = (
                "huggingface_organizations index module unavailable on this deployment"
            )
            job_store.set(existing)
            return
        config, store, client = resources

        items_results: list[dict[str, Any]] = []
        for slug in payload.slugs:
            result = await asyncio.to_thread(
                _ingest_one, slug, config=config, store=store, client=client,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.status = IndexIngestJobStatus.COMPLETED
        finished.completed_at = datetime.now(timezone.utc)
        ingested = sum(1 for r in items_results if r["outcome"] == "ingested")
        skipped_404 = sum(1 for r in items_results if r["outcome"] == "skipped_404")
        skipped_user = sum(1 for r in items_results if r["outcome"] == "skipped_user")
        failed = sum(1 for r in items_results if r["outcome"] == "failed")
        finished.summary = {
            "requested": len(payload.slugs),
            "ingested": ingested,
            "skipped_404": skipped_404,
            "skipped_user": skipped_user,
            "failed": failed,
            "items": items_results,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("huggingface_organizations ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_huggingface_organizations_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    resources = get_or_create_huggingface_organizations_resources(app_state)
    if resources is None:
        return None
    config, store, _ = resources
    from src.index.huggingface_organizations.retrieval.semantic import (  # noqa: PLC0415
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
    "get_or_create_huggingface_organizations_resources",
    "run_huggingface_organizations_ingest_job",
    "run_huggingface_organizations_search",
]
