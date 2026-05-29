"""Async ingest helper for the github_organizations index.

Mirrors `src/v2/indices/github_users.py` — each request item is
fetched via the shared GitHubClient.get_organization and persisted to
the github_organizations DuckDB + Qdrant collection.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    GitHubOrgsIngestRequest,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
)
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "github_organizations"


def get_or_create_github_orgs_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, store, client) on ``app.state``."""

    cached = getattr(app_state, "v2_github_orgs_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.github_repos.ingest.github_client import GitHubClient  # noqa: PLC0415
        from src.index.github_organizations.config import load_config  # noqa: PLC0415
        from src.index.github_organizations.storage.duckdb_store import (  # noqa: PLC0415
            GitHubOrganizationsStore,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_organizations ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        config.require_github()
        store = GitHubOrganizationsStore.open(config.paths.duckdb_path)
        client = GitHubClient(
            api_base=config.github.api_base,
            token=config.github.token,
            cache_path=config.paths.cache_db_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_organizations ingest: resource init failed — %s", exc)
        return None
    app_state.v2_github_orgs_resources = (config, store, client)
    return app_state.v2_github_orgs_resources


def _ingest_one_org(
    login: str, *, config: Any, store: Any, client: Any,
) -> dict[str, Any]:
    """Per-org ingest; never raises."""
    try:
        from src.index.github_organizations.ingest.organizations import (  # noqa: PLC0415
            ingest_single_organization,
        )
        outcome = ingest_single_organization(
            config=config, store=store, client=client, login=login,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_organizations ingest: %s failed — %s", login, exc)
        return {"login": login, "outcome": "failed", "error": str(exc)}
    return {"login": login, "outcome": outcome}


async def run_github_orgs_ingest_job(
    *,
    payload: GitHubOrgsIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each org login and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_github_orgs_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "github_organizations index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, store, client = resources

        items_results: list[dict[str, Any]] = []
        for login in payload.orgs:
            result = await asyncio.to_thread(
                _ingest_one_org,
                login, config=config, store=store, client=client,
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
            "requested": len(payload.orgs),
            "ingested": ingested,
            "skipped_404": skipped_404,
            "skipped_user": skipped_user,
            "failed": failed,
            "items": items_results,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("github_organizations ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_github_orgs_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against the github_organizations index."""
    resources = get_or_create_github_orgs_resources(app_state)
    if resources is None:
        return None
    config, store, _ = resources
    from src.index.github_organizations.retrieval.semantic import (  # noqa: PLC0415
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
    "get_or_create_github_orgs_resources",
    "run_github_orgs_ingest_job",
    "run_github_orgs_search",
]
