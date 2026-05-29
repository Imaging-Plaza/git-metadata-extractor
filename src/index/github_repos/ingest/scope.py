"""Scope resolution: which seed list of `<owner>/<name>` to ingest.

Reads `scope.seeds[<name>]` from the loaded config. Optionally augments
with the OpenAlex `work_github_urls` table when `--from-openalex` is used.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.index.github_repos.config import GitHubIndexConfig

LOGGER = logging.getLogger(__name__)

# Strict GitHub-handle pattern (alphanumerics + single dashes, no leading/trailing dash).
_HANDLE = r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}"
# Repo-name allows dots, underscores, dashes; no spaces or path traversal.
_REPO = r"[A-Za-z0-9._-]{1,100}"
_REPO_ID_RE = re.compile(rf"^({_HANDLE})/({_REPO})$")


@dataclass(slots=True)
class Scope:
    name: str
    repos: list[str]


SCOPE_NOT_CONFIGURED_ERROR = (
    "scope={name} not found in config.scope.seeds. Edit config/index/github.yaml."
)


def _normalize_repo_id(repo_id: str) -> str | None:
    match = _REPO_ID_RE.match(repo_id.strip())
    if not match:
        return None
    owner, name = match.groups()
    # GitHub treats owner names case-insensitively but preserves the
    # canonical case in the API response. Lower-casing is *not* safe for
    # building URLs. We normalise only by stripping whitespace and trailing
    # `.git` to dedupe at the seed-level.
    name = name.removesuffix(".git")
    return f"{owner}/{name}"


def resolve_scope(name: str, config: GitHubIndexConfig) -> Scope:
    seeds = config.scope.seeds.get(name)
    if seeds is None:
        raise SystemExit(SCOPE_NOT_CONFIGURED_ERROR.format(name=name))
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in seeds:
        norm = _normalize_repo_id(raw)
        if not norm:
            LOGGER.warning("scope %s: skipping malformed repo_id %r", name, raw)
            continue
        if norm.lower() in seen:
            continue
        seen.add(norm.lower())
        deduped.append(norm)
    return Scope(name=name, repos=deduped)


def merge_openalex_repos(
    scope: Scope,
    *,
    openalex_db_path: Path,
) -> Scope:
    """Augment the scope with distinct repos from the OpenAlex `work_github_urls` table.

    We only add (`owner/name`) pairs that aren't already in `scope.repos`. The
    OpenAlex DB is the source of truth for URLs discovered in Swiss-affiliated
    works. Logs a single summary line; never auto-promotes the YAML seed.
    """
    if not openalex_db_path.exists():
        LOGGER.warning(
            "merge_openalex_repos: %s not found; skipping bootstrap",
            openalex_db_path,
        )
        return scope
    import duckdb

    seen_lower = {r.lower() for r in scope.repos}
    added: list[str] = []
    con = duckdb.connect(str(openalex_db_path), read_only=True)
    try:
        cur = con.execute(
            "SELECT DISTINCT normalized_url FROM work_github_urls "
            "WHERE normalized_url IS NOT NULL",
        )
        for (url,) in cur.fetchall():
            # normalized_url is e.g. "https://github.com/owner/name"
            tail = str(url).removeprefix("https://github.com/").removeprefix("http://github.com/")
            tail = tail.strip("/")
            norm = _normalize_repo_id(tail)
            if not norm or norm.lower() in seen_lower:
                continue
            added.append(norm)
            seen_lower.add(norm.lower())
    finally:
        con.close()
    LOGGER.info(
        "merge_openalex_repos: added %d repos from %s (kept %d from YAML seed)",
        len(added),
        openalex_db_path,
        len(scope.repos),
    )
    return Scope(name=scope.name, repos=[*scope.repos, *added])
