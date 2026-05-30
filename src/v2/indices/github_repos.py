"""Async ingest helper for the github_repos index, called from `/v2/indices/github_repos/ingest`.

Each item in the request body is dispatched to
:func:`src.index.github_repos.ingest.repos.ingest_single_repo` against a shared
``GitHubReposStore`` + ``GitHubClient`` cached on ``app.state``. A failure on one
repo does not stop the rest; per-repo outcomes land on the job summary.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    GitHubIngestRequest,
    IndexIngestJobStatus,
    IndexSearchRequest,
    IndexSearchResponse,
)
from src.v2.indices._embed_step import run_embed_step
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "github_repos"


def get_or_create_github_repos_resources(app_state: Any) -> Any | None:
    """Lazy-init (config, store, client) on ``app.state``."""

    cached = getattr(app_state, "v2_github_repos_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.github_repos.config import load_config  # noqa: PLC0415
        from src.index.github_repos.ingest.github_client import GitHubClient  # noqa: PLC0415
        from src.index.github_repos.storage.duckdb_store import GitHubReposStore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning("github ingest: index module unavailable — %s", exc)
        return None
    try:
        config = load_config()
        config.require_github()
        store = GitHubReposStore.open(config.paths.duckdb_path)
        client = GitHubClient(
            api_base=config.github.api_base,
            token=config.github.token,
            cache_path=config.paths.cache_db_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github ingest: resource init failed — %s", exc)
        return None
    app_state.v2_github_repos_resources = (config, store, client)
    return app_state.v2_github_repos_resources


def _ingest_one_repo(
    repo: str, *, config: Any, store: Any, client: Any,
) -> dict[str, Any]:
    """Run a single per-repo ingest; never raises."""
    try:
        from src.index.github_repos.ingest.repos import (  # noqa: PLC0415
            ingest_single_repo,
        )
        outcome = ingest_single_repo(
            config=config, store=store, client=client, full_name=repo,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("github ingest: %s failed — %s", repo, exc)
        return {"repo": repo, "outcome": "failed", "error": str(exc)}
    return {"repo": repo, "outcome": outcome}


async def run_github_repos_ingest_job(
    *,
    payload: GitHubIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each repo and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_github_repos_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = "github index module unavailable on this deployment"
            job_store.set(existing)
            return
        config, store, client = resources

        items_results: list[dict[str, Any]] = []
        for repo in payload.repos:
            result = await asyncio.to_thread(
                _ingest_one_repo,
                repo, config=config, store=store, client=client,
            )
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.completed_at = datetime.now(timezone.utc)
        ingested = sum(1 for r in items_results if r["outcome"].startswith("ingested"))
        skipped_404 = sum(1 for r in items_results if r["outcome"] == "skipped_404")
        failed = sum(1 for r in items_results if r["outcome"] == "failed")

        from src.index.github_repos.embed.pipeline import embed_repos  # noqa: PLC0415
        embed_summary = await run_embed_step(
            provider=INDEX_NAME,
            job_id=job_id,
            embed_call=lambda: embed_repos(config=config, store=store),
            checkpoint_store=store,
        )

        finished.status = IndexIngestJobStatus.COMPLETED
        finished.summary = {
            "requested": len(payload.repos),
            "ingested": ingested,
            "skipped_404": skipped_404,
            "failed": failed,
            "items": items_results,
            "embed": embed_summary,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("github ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_github_repos_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a semantic search against the GitHub repos index."""
    resources = get_or_create_github_repos_resources(app_state)
    if resources is None:
        return None
    config, store, _ = resources
    from src.index.github_repos.retrieval.semantic import semantic_search  # noqa: PLC0415
    raw_hits = await asyncio.to_thread(
        semantic_search,
        config=config, query=payload.query,
        top_k=payload.top_k,
        candidate_k=payload.candidate_k or max(payload.top_k * 5, 50),
        filter_payload=payload.filter_payload,
        store=store,
    )
    return IndexSearchResponse(
        index_name="github_repos",
        target=None,
        query=payload.query,
        hits=[hit_from_raw(h) for h in raw_hits],
    )


__all__ = [
    "INDEX_NAME",
    "get_or_create_github_repos_resources",
    "run_github_repos_ingest_job",
    "run_github_repos_search",
]
