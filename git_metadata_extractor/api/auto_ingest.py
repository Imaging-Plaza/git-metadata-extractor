"""Opt-in write-through requests to the sources service."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from git_metadata_extractor.providers.sources_service import (
    SourcesClient,
    current_sources_client,
)

from . import _helpers

logger = logging.getLogger(__name__)
_PENDING_TASKS: set[asyncio.Task] = set()


def _github_account_login_from_url(normalized_url: Any) -> str | None:
    """Extract a bare GitHub login from a normalised user/org URL, or None.

    Accepts the same URL shapes the classifier emits for user/org
    targets: `https://github.com/<login>` or `https://github.com/orgs/<login>`.
    Returns the bare handle on success, or None if the URL has the
    wrong host, an extra path segment (which would indicate a repo),
    or is malformed.
    """
    if not isinstance(normalized_url, str) or not normalized_url.startswith(
        ("https://github.com/", "http://github.com/")
    ):
        return None
    rest = (
        normalized_url.removeprefix("https://github.com/")
        .removeprefix("http://github.com/")
        .strip("/")
    )
    if not rest:
        return None
    # `/orgs/<login>` is GitHub's web UI URL for an org; strip the prefix.
    if rest.startswith("orgs/"):
        rest = rest[len("orgs/") :]
    # User/org URLs are a single path segment — anything with a `/`
    # left is a repo and shouldn't reach this helper.
    if "/" in rest:
        return None
    return rest or None


def _schedule_ingest(
    *, index: str, target: str, run_id: str, sources_client: SourcesClient | None
) -> None:
    async def submit() -> None:
        client = sources_client or current_sources_client()
        if client is None:
            logger.warning(
                "%s auto-ingest (run_id=%s): sources service unavailable", index, run_id
            )
            return
        try:
            result = await client.operation(
                "ensure-indexed",
                {
                    "index": index,
                    "target": target,
                    "skip_existing": True,
                    "embed": True,
                },
            )
        except Exception:  # noqa: BLE001 - optional background work fails open
            logger.warning(
                "%s auto-ingest (run_id=%s): sources submission failed", index, run_id
            )
            return
        if result.status != "ok":
            logger.warning(
                "%s auto-ingest (run_id=%s): sources submission %s",
                index,
                run_id,
                result.status,
            )
            return
        logger.info(
            "%s auto-ingest (run_id=%s): submitted job_id=%s status=%s",
            index,
            run_id,
            result.data.get("job_id"),
            result.data.get("status"),
        )

    coroutine = submit()
    try:
        task = asyncio.create_task(coroutine)
    except RuntimeError:
        coroutine.close()
        return
    _PENDING_TASKS.add(task)
    task.add_done_callback(_PENDING_TASKS.discard)


def _maybe_schedule_github_repos_auto_ingest(
    *, classification: Any, run_id: str, sources_client: SourcesClient | None = None
) -> None:
    if not _helpers._auto_ingest_enabled(  # noqa: SLF001
        "V2_GITHUB_REPOS_RAG_AUTO_INGEST", "V2_GITHUB_RAG_AUTO_INGEST"
    ):
        return
    if (
        getattr(getattr(classification, "detected_type", None), "value", None)
        != "repository"
    ):
        return
    url = getattr(classification, "normalized_url", None)
    if not isinstance(url, str) or not url.startswith(
        ("https://github.com/", "http://github.com/")
    ):
        return
    target = (
        url.removeprefix("https://github.com/")
        .removeprefix("http://github.com/")
        .strip("/")
    )
    if target.count("/") != 1 or not all(target.split("/")):
        return
    _schedule_ingest(
        index="github_repos",
        target=target,
        run_id=run_id,
        sources_client=sources_client,
    )


def _maybe_schedule_account_ingest(  # noqa: PLR0913
    *,
    classification: Any,
    run_id: str,
    sources_client: SourcesClient | None,
    kind: str,
    flag: str,
    index: str,
) -> None:
    if os.getenv(flag, "false").strip().lower() != "true":
        return
    if getattr(getattr(classification, "detected_type", None), "value", None) != kind:
        return
    login = _github_account_login_from_url(
        getattr(classification, "normalized_url", None)
    )
    if login is not None:
        _schedule_ingest(
            index=index, target=login, run_id=run_id, sources_client=sources_client
        )


def _maybe_schedule_github_users_auto_ingest(
    *, classification: Any, run_id: str, sources_client: SourcesClient | None = None
) -> None:
    _maybe_schedule_account_ingest(
        classification=classification,
        run_id=run_id,
        sources_client=sources_client,
        kind="user",
        flag="V2_GITHUB_USERS_RAG_AUTO_INGEST",
        index="github_users",
    )


def _maybe_schedule_github_orgs_auto_ingest(
    *, classification: Any, run_id: str, sources_client: SourcesClient | None = None
) -> None:
    _maybe_schedule_account_ingest(
        classification=classification,
        run_id=run_id,
        sources_client=sources_client,
        kind="organization",
        flag="V2_GITHUB_ORGS_RAG_AUTO_INGEST",
        index="github_organizations",
    )
