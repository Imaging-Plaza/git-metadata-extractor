"""Iterate the active scope, fetch each repo, persist to DuckDB + cards/."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.index.github_repos.ingest.github_client import GitHubClient
from src.index.github_repos.models import ContributorEntry, RepoRecord
from src.v2.canonicalization.github import github_repo_iri

if TYPE_CHECKING:
    from src.index.github_repos.config import GitHubIndexConfig
    from src.index.github_repos.ingest.scope import Scope
    from src.index.github_repos.storage.duckdb_store import GitHubReposStore

LOGGER = logging.getLogger(__name__)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        # GitHub returns RFC 3339 / ISO 8601 with trailing 'Z'.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _record_from_payload(
    *,
    full_name: str,
    repo_payload: dict[str, Any],
    languages: dict[str, int],
    contributors: list[dict[str, Any]],
    readme_text: str | None,
    readme_path: str | None,
) -> RepoRecord:
    owner_block = repo_payload.get("owner") or {}
    license_block = repo_payload.get("license") or {}
    owner = owner_block.get("login") or full_name.split("/", 1)[0]
    name = repo_payload.get("name") or full_name.split("/", 1)[1]
    return RepoRecord(
        # v3.0.0: the id is the canonical GitHub URL (owner/name kept as their
        # own bare fields). Consistent with zenodo/openalex/ror stores, the old
        # HF monolith, and the extract pipeline's pulse:* github IRIs.
        repo_id=github_repo_iri(full_name) or full_name,
        owner=str(owner),
        name=str(name),
        default_branch=repo_payload.get("default_branch"),
        description=repo_payload.get("description"),
        homepage=repo_payload.get("homepage"),
        primary_language=repo_payload.get("language"),
        languages=languages,
        topics=list(repo_payload.get("topics") or []),
        license_spdx=license_block.get("spdx_id") if isinstance(license_block, dict) else None,
        is_fork=bool(repo_payload.get("fork", False)),
        is_archived=bool(repo_payload.get("archived", False)),
        is_private=bool(repo_payload.get("private", False)),
        stargazers_count=int(repo_payload.get("stargazers_count") or 0),
        forks_count=int(repo_payload.get("forks_count") or 0),
        watchers_count=int(repo_payload.get("watchers_count") or 0),
        open_issues_count=int(repo_payload.get("open_issues_count") or 0),
        size_kb=int(repo_payload.get("size") or 0),
        created_at=_parse_iso(repo_payload.get("created_at")),
        pushed_at=_parse_iso(repo_payload.get("pushed_at")),
        readme_path=readme_path,
        readme_text=readme_text,
        contributors=[
            ContributorEntry(
                login=str(c.get("login")),
                contributions=int(c.get("contributions") or 0),
            )
            for c in contributors
            if c.get("login")
        ],
        raw=repo_payload,
    )


def _persist_readme(*, owner: str, name: str, text: str, cards_dir: Path) -> str:
    target_dir = cards_dir / owner / name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "README.md"
    target.write_text(text, encoding="utf-8")
    return str(target.relative_to(cards_dir))


def ingest_single_repo(
    *,
    config: GitHubIndexConfig,
    store: GitHubReposStore,
    client: GitHubClient,
    full_name: str,
) -> str:
    """Fetch + upsert one repository. Returns ``"ingested" | "skipped_404"``.

    Increments the underlying DuckDB row and (when present) writes the README
    snapshot to the cards directory. Pure side-effect; the bulk function and
    the HTTP route share this path so behaviour stays consistent.
    """
    repo_payload = client.get_repository(full_name)
    if not isinstance(repo_payload, dict):
        LOGGER.warning("ingest skip: repo not found or unreachable: %s", full_name)
        return "skipped_404"
    languages = client.get_languages(full_name)
    contributors = client.get_contributors(full_name)
    readme_text, readme_path = client.get_readme(
        full_name,
        max_bytes=config.github.readme_max_bytes,
    )
    record = _record_from_payload(
        full_name=full_name,
        repo_payload=repo_payload,
        languages=languages,
        contributors=contributors,
        readme_text=readme_text,
        readme_path=readme_path,
    )
    if readme_text:
        stored_path = _persist_readme(
            owner=record.owner,
            name=record.name,
            text=readme_text,
            cards_dir=config.paths.cards_dir,
        )
        # Replace the GitHub-side `readme_path` (e.g. `README.md` or
        # `docs/README.rst`) with the on-disk relative path so downstream
        # code can find the file unambiguously.
        record.readme_path = stored_path
    store.upsert_repo(record)
    LOGGER.info(
        "ingested %s (stars=%d lang=%s readme=%s)",
        full_name,
        record.stargazers_count,
        record.primary_language or "-",
        "yes" if readme_text else "no",
    )
    return "ingested" if readme_text else "ingested_no_readme"


def ingest_repos(
    *,
    config: GitHubIndexConfig,
    store: GitHubReposStore,
    scope: Scope,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch metadata + README for every repo in scope and upsert to DuckDB.

    Returns a summary dict: counts of {seen, ingested, skipped_404, no_readme}.
    """
    config.require_github()
    client = GitHubClient(
        api_base=config.github.api_base,
        token=config.github.token,
        cache_path=config.paths.cache_db_path,
    )
    seen = ingested = skipped_404 = no_readme = 0
    repos_to_process = scope.repos[:limit] if limit is not None else scope.repos
    for full_name in repos_to_process:
        seen += 1
        outcome = ingest_single_repo(
            config=config, store=store, client=client, full_name=full_name,
        )
        if outcome == "skipped_404":
            skipped_404 += 1
            continue
        ingested += 1
        if outcome == "ingested_no_readme":
            no_readme += 1
    return {
        "seen": seen,
        "ingested": ingested,
        "skipped_404": skipped_404,
        "no_readme": no_readme,
    }
