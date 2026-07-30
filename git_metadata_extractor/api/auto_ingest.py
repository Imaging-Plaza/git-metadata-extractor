from __future__ import annotations

import asyncio
import logging
import os
import threading
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any



MIN_SUPPORTED_PYTHON = (3, 10)
PACKAGE_NAME = "git-metadata-extractor"
try:
    PACKAGE_VERSION = package_version(PACKAGE_NAME)
except PackageNotFoundError:
    PACKAGE_VERSION = "unknown"

MIN_SUBRESOURCE_PATH_SEGMENTS = 3
SUBRESOURCE_SEGMENT_INDEX = 2
JSONLD_CONTEXT_FALLBACK = {
    "schema": "http://schema.org/",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "org": "http://www.w3.org/ns/org#",
}

logger = logging.getLogger(__name__)


from . import _helpers

_GITHUB_REPOS_AUTO_INGEST_LOCK = threading.Lock()
_GITHUB_USERS_AUTO_INGEST_LOCK = threading.Lock()
_GITHUB_ORGS_AUTO_INGEST_LOCK = threading.Lock()
_HF_PAPERS_AUTO_INGEST_LOCK = threading.Lock()


def _hf_papers_arxiv_id_from_url(normalized_url: Any) -> str | None:
    """Extract an arXiv id from a `huggingface.co/papers/<arxiv_id>` URL.

    Only fires for HF Papers URLs — does NOT fire for raw `arxiv.org`
    URLs or arXiv DOIs, by design (the user opted in for HF Papers
    URLs specifically). Uses the canonical arXiv id normaliser so
    version suffixes are stripped.
    """
    if not isinstance(normalized_url, str) or "huggingface.co/papers/" not in normalized_url:
        return None
    try:
        from open_pulse_sources.index.huggingface_papers.ingest.hf_papers_client import (  # noqa: PLC0415
            normalize_arxiv_id,
        )
    except Exception:  # noqa: BLE001
        return None
    return normalize_arxiv_id(normalized_url)


def _github_account_login_from_url(normalized_url: Any) -> str | None:
    """Extract a bare GitHub login from a normalised user/org URL, or None.

    Accepts the same URL shapes the classifier emits for user/org
    targets: `https://github.com/<login>` or `https://github.com/orgs/<login>`.
    Returns the bare handle on success, or None if the URL has the
    wrong host, an extra path segment (which would indicate a repo),
    or is malformed.
    """
    if not isinstance(normalized_url, str) or "github.com/" not in normalized_url:
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
        rest = rest[len("orgs/"):]
    # User/org URLs are a single path segment — anything with a `/`
    # left is a repo and shouldn't reach this helper.
    if "/" in rest:
        return None
    return rest or None


def _maybe_schedule_github_repos_auto_ingest(
    *,
    classification: Any,
    run_id: str,
) -> None:
    """Schedule a background GitHub RAG ingest when the operator opts in.

    Gates:
    - `V2_GITHUB_REPOS_RAG_AUTO_INGEST=true` env var (off by default — every
      existing deployment keeps its current behaviour).
    - The extract target must be a repository (we have no index for
      user/org/article cards yet).
    - `classification.normalized_url` must be a public github.com repo.
      Private/unreachable repos surface as `skipped_404` inside
      `ingest_single_repo` and emit one warning; no crash.

    Concurrency: a module-level `threading.Lock` serialises DuckDB
    writes across uvicorn worker tasks. The single-repo ingest is
    fast (~1-3s) so contention is negligible.
    """
    if not _helpers._auto_ingest_enabled(
        "V2_GITHUB_REPOS_RAG_AUTO_INGEST", "V2_GITHUB_RAG_AUTO_INGEST",
    ):
        return
    if not hasattr(classification, "detected_type"):
        return
    if str(classification.detected_type.value).lower() != "repository":
        return
    normalized_url = getattr(classification, "normalized_url", None)
    if not isinstance(normalized_url, str) or "github.com/" not in normalized_url:
        return
    full_name = normalized_url.removeprefix("https://github.com/").removeprefix(
        "http://github.com/",
    ).strip("/")
    if not full_name or full_name.count("/") != 1:
        return

    async def _run() -> None:
        try:
            from open_pulse_sources.index.github_repos.config import (
                load_config as load_github_config,
            )
            from open_pulse_sources.index.github_repos.embed.pipeline import (
                embed_repos,
            )
            from open_pulse_sources.index.github_repos.ingest.github_client import (
                GitHubClient,
            )
            from open_pulse_sources.index.github_repos.ingest.repos import (
                ingest_single_repo,
            )
            from open_pulse_sources.index.github_repos.storage.duckdb_store import (
                GitHubReposStore,
            )
        except Exception:
            logger.exception(
                "github auto-ingest (run_id=%s, repo=%s): module import failed",
                run_id, full_name,
            )
            return

        def _do_ingest() -> tuple[str, int]:
            cfg = load_github_config()
            cfg.require_github()
            with _GITHUB_REPOS_AUTO_INGEST_LOCK:
                store = GitHubReposStore.open(cfg.paths.duckdb_path)
                try:
                    existing = store.fetch_repo(full_name)
                    if existing is not None:
                        return ("skipped_already_indexed", 0)
                    client = GitHubClient(
                        api_base=cfg.github.api_base,
                        token=cfg.github.token,
                        cache_path=cfg.paths.cache_db_path,
                    )
                    outcome = ingest_single_repo(
                        config=cfg, store=store, client=client, full_name=full_name,
                    )
                    if outcome == "skipped_404":
                        return ("skipped_404", 0)
                    embed_summary = embed_repos(config=cfg, store=store, limit=None)
                    return (outcome, int(embed_summary.get("repos", 0)))
                finally:
                    store.close()

        try:
            outcome, embedded = await asyncio.to_thread(_do_ingest)
        except Exception:
            logger.exception(
                "github auto-ingest (run_id=%s, repo=%s): failed",
                run_id, full_name,
            )
            return
        logger.info(
            "github auto-ingest (run_id=%s, repo=%s): %s (chunks_embedded=%d)",
            run_id, full_name, outcome, embedded,
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        # No running event loop (e.g. unit tests that call extract
        # synchronously). Skip silently — the auto-ingest is a
        # non-essential background enrichment.
        return


def _maybe_schedule_github_users_auto_ingest(
    *,
    classification: Any,
    run_id: str,
) -> None:
    """Schedule a background ingest into the github_users index when
    the operator opts in via `V2_GITHUB_USERS_RAG_AUTO_INGEST=true`.

    Same gating shape as `_maybe_schedule_github_repos_auto_ingest` but
    fires only for `detected_type == "user"` targets. Org-typed
    extracts are handled by the sibling helper below.
    """
    if os.getenv("V2_GITHUB_USERS_RAG_AUTO_INGEST", "false").strip().lower() != "true":
        return
    if not hasattr(classification, "detected_type"):
        return
    if str(classification.detected_type.value).lower() != "user":
        return
    login = _github_account_login_from_url(
        getattr(classification, "normalized_url", None),
    )
    if login is None:
        return

    async def _run() -> None:
        try:
            from open_pulse_sources.index.github_repos.ingest.github_client import (
                GitHubClient,
            )
            from open_pulse_sources.index.github_users.config import load_config  # noqa: PLC0415
            from open_pulse_sources.index.github_users.embed.pipeline import (
                embed_users,
            )
            from open_pulse_sources.index.github_users.ingest.users import (
                ingest_single_user,
            )
            from open_pulse_sources.index.github_users.storage.duckdb_store import (  # noqa: PLC0415
                GitHubUsersStore,
            )
        except Exception:
            logger.exception(
                "github_users auto-ingest (run_id=%s, login=%s): module import failed",
                run_id, login,
            )
            return

        def _do_ingest() -> tuple[str, int]:
            cfg = load_config()
            cfg.require_github()
            with _GITHUB_USERS_AUTO_INGEST_LOCK:
                store = GitHubUsersStore.open(cfg.paths.duckdb_path)
                try:
                    existing = store.fetch_user(login)
                    if existing is not None:
                        return ("skipped_already_indexed", 0)
                    client = GitHubClient(
                        api_base=cfg.github.api_base,
                        token=cfg.github.token,
                        cache_path=cfg.paths.cache_db_path,
                    )
                    outcome = ingest_single_user(
                        config=cfg, store=store, client=client, login=login,
                    )
                    if outcome in {"skipped_404", "skipped_org"}:
                        return (outcome, 0)
                    embed_summary = embed_users(config=cfg, store=store, limit=None)
                    return (outcome, int(embed_summary.get("users", 0)))
                finally:
                    store.close()

        try:
            outcome, embedded = await asyncio.to_thread(_do_ingest)
        except Exception:
            logger.exception(
                "github_users auto-ingest (run_id=%s, login=%s): failed",
                run_id, login,
            )
            return
        logger.info(
            "github_users auto-ingest (run_id=%s, login=%s): %s (chunks_embedded=%d)",
            run_id, login, outcome, embedded,
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        return


def _maybe_schedule_github_orgs_auto_ingest(
    *,
    classification: Any,
    run_id: str,
) -> None:
    """Schedule a background ingest into the github_organizations index
    when `V2_GITHUB_ORGS_RAG_AUTO_INGEST=true`.

    Fires only for `detected_type == "organization"` targets — uses
    the v2 detected-type enum (`organization`, not `org`).
    """
    if os.getenv("V2_GITHUB_ORGS_RAG_AUTO_INGEST", "false").strip().lower() != "true":
        return
    if not hasattr(classification, "detected_type"):
        return
    if str(classification.detected_type.value).lower() != "organization":
        return
    login = _github_account_login_from_url(
        getattr(classification, "normalized_url", None),
    )
    if login is None:
        return

    async def _run() -> None:
        try:
            from open_pulse_sources.index.github_organizations.config import (
                load_config,
            )
            from open_pulse_sources.index.github_organizations.embed.pipeline import (  # noqa: PLC0415
                embed_organizations,
            )
            from open_pulse_sources.index.github_organizations.ingest.organizations import (  # noqa: PLC0415
                ingest_single_organization,
            )
            from open_pulse_sources.index.github_organizations.storage.duckdb_store import (  # noqa: PLC0415
                GitHubOrganizationsStore,
            )
            from open_pulse_sources.index.github_repos.ingest.github_client import (
                GitHubClient,
            )
        except Exception:
            logger.exception(
                "github_organizations auto-ingest (run_id=%s, login=%s): module import failed",
                run_id, login,
            )
            return

        def _do_ingest() -> tuple[str, int]:
            cfg = load_config()
            cfg.require_github()
            with _GITHUB_ORGS_AUTO_INGEST_LOCK:
                store = GitHubOrganizationsStore.open(cfg.paths.duckdb_path)
                try:
                    existing = store.fetch_organization(login)
                    if existing is not None:
                        return ("skipped_already_indexed", 0)
                    client = GitHubClient(
                        api_base=cfg.github.api_base,
                        token=cfg.github.token,
                        cache_path=cfg.paths.cache_db_path,
                    )
                    outcome = ingest_single_organization(
                        config=cfg, store=store, client=client, login=login,
                    )
                    if outcome in {"skipped_404", "skipped_user"}:
                        return (outcome, 0)
                    embed_summary = embed_organizations(
                        config=cfg, store=store, limit=None,
                    )
                    return (outcome, int(embed_summary.get("organizations", 0)))
                finally:
                    store.close()

        try:
            outcome, embedded = await asyncio.to_thread(_do_ingest)
        except Exception:
            logger.exception(
                "github_organizations auto-ingest (run_id=%s, login=%s): failed",
                run_id, login,
            )
            return
        logger.info(
            "github_organizations auto-ingest (run_id=%s, login=%s): %s (chunks_embedded=%d)",
            run_id, login, outcome, embedded,
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        return


def _maybe_schedule_huggingface_papers_auto_ingest(
    *,
    classification: Any,
    run_id: str,
) -> None:
    """Schedule a background ingest into the huggingface_papers index
    when `V2_HF_PAPERS_RAG_AUTO_INGEST=true` AND the extract target
    is a `huggingface.co/papers/<arxiv_id>` URL.

    Unlike the github_users / github_organizations helpers, this one
    does NOT check `classification.detected_type` — HF Papers URLs
    don't necessarily have a dedicated detected_type, so we gate
    purely on the URL pattern. The narrow URL match is the safety
    net: only true HF Papers URLs trigger; raw arXiv URLs and DOIs
    are skipped (per the operator's choice when this feature was
    designed).
    """
    if os.getenv("V2_HF_PAPERS_RAG_AUTO_INGEST", "false").strip().lower() != "true":
        return
    if not hasattr(classification, "normalized_url"):
        return
    arxiv_id = _hf_papers_arxiv_id_from_url(
        getattr(classification, "normalized_url", None),
    )
    if arxiv_id is None:
        return

    async def _run() -> None:
        try:
            from open_pulse_sources.index.huggingface_papers.config import load_config  # noqa: PLC0415
            from open_pulse_sources.index.huggingface_papers.embed.pipeline import (
                embed_papers,
            )
            from open_pulse_sources.index.huggingface_papers.ingest.hf_papers_client import (  # noqa: PLC0415
                HFPapersClient,
            )
            from open_pulse_sources.index.huggingface_papers.ingest.papers import (  # noqa: PLC0415
                ingest_single_paper,
            )
            from open_pulse_sources.index.huggingface_papers.storage.duckdb_store import (  # noqa: PLC0415
                HuggingFacePapersStore,
            )
        except Exception:
            logger.exception(
                "huggingface_papers auto-ingest (run_id=%s, arxiv_id=%s): module import failed",
                run_id, arxiv_id,
            )
            return

        def _do_ingest() -> tuple[str, int]:
            cfg = load_config()
            with _HF_PAPERS_AUTO_INGEST_LOCK:
                store = HuggingFacePapersStore.open(cfg.paths.duckdb_path)
                try:
                    existing = store.fetch_paper(arxiv_id)
                    if existing is not None:
                        return ("skipped_already_indexed", 0)
                    client = HFPapersClient(
                        api_base=cfg.huggingface.api_base,
                        token=cfg.huggingface.token,
                        cache_path=cfg.paths.cache_db_path,
                    )
                    outcome = ingest_single_paper(
                        config=cfg, store=store, client=client, arxiv_id=arxiv_id,
                    )
                    if outcome == "skipped_404":
                        return (outcome, 0)
                    embed_summary = embed_papers(config=cfg, store=store, limit=None)
                    return (outcome, int(embed_summary.get("papers", 0)))
                finally:
                    store.close()

        try:
            outcome, embedded = await asyncio.to_thread(_do_ingest)
        except Exception:
            logger.exception(
                "huggingface_papers auto-ingest (run_id=%s, arxiv_id=%s): failed",
                run_id, arxiv_id,
            )
            return
        logger.info(
            "huggingface_papers auto-ingest (run_id=%s, arxiv_id=%s): %s (chunks_embedded=%d)",
            run_id, arxiv_id, outcome, embedded,
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        return


