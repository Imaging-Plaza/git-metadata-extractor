"""Async ingest helper for the github_users index.

Each item in the request body is fetched via the shared
``GitHubClient.get_user`` and persisted to the github_users DuckDB +
Qdrant collection. Failures on one user do not stop the rest.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    GitHubUsersIngestRequest,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
)
from src.v2.indices._embed_step import run_embed_step
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "github_users"


def get_or_create_github_users_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, store, client) on ``app.state``."""

    cached = getattr(app_state, "v2_github_users_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.github_repos.ingest.github_client import GitHubClient  # noqa: PLC0415
        from src.index.github_users.config import load_config  # noqa: PLC0415
        from src.index.github_users.storage.duckdb_store import (  # noqa: PLC0415
            GitHubUsersStore,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_users ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        config.require_github()
        store = GitHubUsersStore.open(config.paths.duckdb_path)
        client = GitHubClient(
            api_base=config.github.api_base,
            token=config.github.token,
            cache_path=config.paths.cache_db_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_users ingest: resource init failed — %s", exc)
        return None
    app_state.v2_github_users_resources = (config, store, client)
    return app_state.v2_github_users_resources


def _ingest_one_user(
    login: str, *, config: Any, store: Any, client: Any,
) -> dict[str, Any]:
    """Per-user ingest; never raises."""
    try:
        from src.index.github_users.ingest.users import (  # noqa: PLC0415
            ingest_single_user,
        )
        outcome = ingest_single_user(
            config=config, store=store, client=client, login=login,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github_users ingest: %s failed — %s", login, exc)
        return {"login": login, "outcome": "failed", "error": str(exc)}
    return {"login": login, "outcome": outcome}


async def run_github_users_ingest_job(
    *,
    payload: GitHubUsersIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each login and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_github_users_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "github_users index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, store, client = resources

        items_results: list[dict[str, Any]] = []
        for login in payload.logins:
            result = await asyncio.to_thread(
                _ingest_one_user,
                login, config=config, store=store, client=client,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
        ingested = sum(1 for r in items_results if r["outcome"] == "ingested")
        skipped_404 = sum(1 for r in items_results if r["outcome"] == "skipped_404")
        skipped_org = sum(1 for r in items_results if r["outcome"] == "skipped_org")
        failed = sum(1 for r in items_results if r["outcome"] == "failed")

        from src.index.github_users.embed.pipeline import embed_users  # noqa: PLC0415
        embed_summary = await run_embed_step(
            provider=INDEX_NAME,
            job_id=job_id,
            embed_call=lambda: embed_users(config=config, store=store),
        )

        finished.status = IndexIngestJobStatus.COMPLETED
        finished.summary = {
            "requested": len(payload.logins),
            "ingested": ingested,
            "skipped_404": skipped_404,
            "skipped_org": skipped_org,
            "failed": failed,
            "items": items_results,
            "embed": embed_summary,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("github_users ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_github_users_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against the github_users index."""
    resources = get_or_create_github_users_resources(app_state)
    if resources is None:
        return None
    config, store, _ = resources
    from src.index.github_users.retrieval.semantic import (  # noqa: PLC0415
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
    "get_or_create_github_users_resources",
    "run_github_users_ingest_job",
    "run_github_users_search",
]
