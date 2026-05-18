"""Async ingest helper for the ORCID index, called from `/v2/indices/orcid/ingest`."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
    OrcidIngestRequest,
)
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "orcid"


def get_or_create_orcid_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, store, provider) on ``app.state``."""

    cached = getattr(app_state, "v2_orcid_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.orcid.config import load_config  # noqa: PLC0415
        from src.index.orcid.ingest.orcid_client import (  # noqa: PLC0415
            build_orcid_provider,
        )
        from src.index.orcid.storage.duckdb_store import (  # noqa: PLC0415
            OrcidDuckDBStore,
        )
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("orcid ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        store = OrcidDuckDBStore.open(config.paths.duckdb_path)
        provider = build_orcid_provider(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("orcid ingest: resource init failed — %s", exc)
        return None
    app_state.v2_orcid_resources = (config, store, provider)
    return app_state.v2_orcid_resources


def _ingest_one_orcid(
    orcid_id: str, *, config: Any, store: Any, provider: Any,
) -> dict[str, Any]:
    """Run a single per-orcid ingest; never raises."""
    try:
        from src.index.orcid.ingest.persons import ingest_single_orcid  # noqa: PLC0415
        outcome = ingest_single_orcid(
            config=config,
            store=store,
            provider=provider,
            orcid_id=orcid_id,
            scope="switzerland",
            discovered_via="api_post",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("orcid ingest: %s failed — %s", orcid_id, exc)
        return {"orcid_id": orcid_id, "outcome": "error", "error": str(exc)}
    return {"orcid_id": orcid_id, "outcome": outcome}


async def run_orcid_ingest_job(
    *,
    payload: OrcidIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each ORCID id and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_orcid_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "orcid index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, store, provider = resources

        items_results: list[dict[str, Any]] = []
        for orcid_id in payload.orcid_ids:
            result = await asyncio.to_thread(
                _ingest_one_orcid,
                orcid_id, config=config, store=store, provider=provider,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.status = IndexIngestJobStatus.COMPLETED
        finished.completed_at = datetime.now(timezone.utc)
        in_scope = sum(1 for r in items_results if r["outcome"] == "in_scope")
        out_of_scope = sum(1 for r in items_results if r["outcome"] == "out_of_scope")
        not_found = sum(1 for r in items_results if r["outcome"] == "not_found")
        errors = sum(1 for r in items_results if r["outcome"] == "error")
        finished.summary = {
            "requested": len(payload.orcid_ids),
            "in_scope": in_scope,
            "out_of_scope": out_of_scope,
            "not_found": not_found,
            "errors": errors,
            "items": items_results,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("orcid ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_orcid_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against the ORCID persons index."""
    resources = get_or_create_orcid_resources(app_state)
    if resources is None:
        return None
    config, store, _ = resources
    entity_type = payload.target or "persons"
    from src.index.orcid.retrieval.semantic import semantic_search  # noqa: PLC0415
    raw_hits = await asyncio.to_thread(
        semantic_search,
        config=config, query=payload.query, entity_type=entity_type,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k or max(payload.top_k * 5, 50),
        filter_payload=payload.filter_payload,
        store=store,
    )
    # ORCID hits carry the person under the `person` key rather than `entity`.
    return IndexSearchResponse(
        index_name="orcid",
        target=entity_type,
        query=payload.query,
        hits=[hit_from_raw(h, entity_key="person") for h in raw_hits],
    )


__all__ = [
    "INDEX_NAME",
    "get_or_create_orcid_resources",
    "run_orcid_ingest_job",
    "run_orcid_search",
]
