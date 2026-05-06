from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from src.v2.observation.query_log import record_query

if TYPE_CHECKING:
    from src.v2.ingest.providers.base import GitHubProvider

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

_DESCRIPTION = (
    "Inspect the dependency manifests parsed by GitHub's dependency graph for "
    "a repository. Returns a flat list of packages parsed from the SPDX SBOM, "
    "each entry shaped as {name, ecosystem, version, spdxId}. "
    "Ecosystem values follow purl conventions (pypi, npm, cargo, maven, "
    "githubactions, ...). "
    "Optional: pass `ecosystem` to keep only packages from that ecosystem, "
    "and/or `name_contains` to keep only packages whose name contains the "
    "given substring (case-insensitive). "
    "Call this tool when dependency information is relevant to your "
    "assessment — e.g. ecosystem, scope, research-software classification, "
    "or technology stack inference. Skip when the README and metadata are "
    "already sufficient: the call hits the GitHub API and is not free. "
    "Returns an empty list when the repository has no SBOM available "
    "(dependency graph disabled, private without scope, or no parsed "
    "manifests)."
)


def make_query_dependencies_tool(github_provider: GitHubProvider) -> Tool:
    """Create a pydantic-ai Tool that fetches a repository's parsed dependencies."""

    def query_dependencies(
        full_name: str,
        ecosystem: str | None = None,
        name_contains: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict[str, Any]]:
        """Return parsed dependency entries from a repository's SPDX SBOM.

        Args:
            full_name: GitHub repository handle in ``owner/repo`` form.
            ecosystem: Optional purl ecosystem filter (e.g. ``"pypi"``,
                ``"npm"``). Case-insensitive exact match.
            name_contains: Optional case-insensitive substring filter on
                the package ``name``.
            limit: Maximum number of entries to return (1..500, default 50).
        """
        logger.info(
            "tool call: query_dependencies — full_name=%r ecosystem=%r "
            "name_contains=%r limit=%d",
            full_name,
            ecosystem,
            name_contains,
            limit,
        )
        record_query(service="github.repository_sbom", query=full_name)
        bounded_limit = max(1, min(limit, MAX_LIMIT))
        dependencies = github_provider.get_repository_sbom(full_name)
        if not dependencies:
            return []

        ecosystem_filter = ecosystem.strip().lower() if isinstance(ecosystem, str) else None
        name_filter = (
            name_contains.strip().lower() if isinstance(name_contains, str) else None
        )

        filtered: list[dict[str, Any]] = []
        for entry in dependencies:
            if ecosystem_filter:
                entry_ecosystem = entry.get("ecosystem")
                if not isinstance(entry_ecosystem, str) or entry_ecosystem.lower() != ecosystem_filter:
                    continue
            if name_filter:
                entry_name = entry.get("name")
                if not isinstance(entry_name, str) or name_filter not in entry_name.lower():
                    continue
            filtered.append(dict(entry))
            if len(filtered) >= bounded_limit:
                break
        return filtered

    return Tool(
        query_dependencies,
        name="query_dependencies",
        description=_DESCRIPTION,
    )
