"""GitHub registration with the federated discover/hydrate registries.

Discover sources
----------------

- ``dependents`` — scrape ``github.com/<owner>/<repo>/network/dependents``
  to discover repos that depend on a given target (uses the existing
  module under ``src/module/dependents/``).
- ``from-references`` — placeholder for future "GitHub URLs found in
  works abstracts" cross-index discovery (currently produced by
  ``openalex find-github`` populating ``work_github_urls``).

Hydrate seed types
------------------

- ``github_repo`` — fetch repo metadata via ``ingest_repos`` (delegates
  to GitHub REST + commit / contributor enrichment).
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from src.index._federated.dh_registry import register_discoverer, register_hydrator
from src.index._federated.protocols import (
    HydrationSummary,
    IndexDiscoverer,
    IndexHydrator,
    Seed,
)

LOGGER = logging.getLogger(__name__)


class GitHubDiscoverer:
    name = "github"
    accepted_sources = ("dependents", "from-references")

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        if source not in self.accepted_sources:
            message = f"GitHub: unknown source {source!r}. Accepted: {list(self.accepted_sources)}"
            raise ValueError(message)

        if source == "dependents":
            target = opts.get("target")
            if not target:
                message = "GitHub.dependents requires --opt target=owner/repo"
                raise ValueError(message)
            try:
                from src.module.dependents import scrape_dependents
            except ImportError:
                LOGGER.warning("src.module.dependents not importable; cannot run dependents discover")
                return
            for entry in scrape_dependents(target):
                yield Seed(
                    id=f"https://github.com/{entry}",
                    seed_type="github_repo",
                    source=f"dependents:{target}",
                    hint={"target": target},
                )
            return

        # source == "from-references"
        # Pull github URLs that OpenAlex's find-github discovered into work_github_urls.
        try:
            from src.index.openalex.storage.duckdb_store import DuckDBStore as OAStore
        except ImportError:
            LOGGER.warning("OpenAlex DB not available for from-references discover")
            return
        store = OAStore.open()
        cur = store.connect()
        rows = cur.execute(
            "SELECT DISTINCT normalized_url FROM work_github_urls "
            "WHERE normalized_url IS NOT NULL"
        ).fetchall()
        for (url,) in rows:
            yield Seed(
                id=url,
                seed_type="github_repo",
                source="from-references",
            )


class GitHubHydrator:
    name = "github"
    accepted_seed_types = ("github_repo",)

    def hydrate(self, seeds, *, only_unfetched: bool = True) -> HydrationSummary:
        # TODO: lift `ingest_repos` into a function that accepts a list of
        # github URLs / owner-repo pairs. Currently it operates on a config-
        # driven list. v1 returns a stub summary.
        materialised = list(seeds)
        LOGGER.warning(
            "github: hydrate is a stub (received %d seeds). "
            "Wire to ingest_repos in a follow-up.",
            len(materialised),
        )
        return HydrationSummary(skipped_existing=len(materialised))


register_discoverer(GitHubDiscoverer())
register_hydrator(GitHubHydrator())
