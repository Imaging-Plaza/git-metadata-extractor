"""Async RAG provider over the GitHub repositories Qdrant index.

Single collection: ``github_repos``. The search flow lives in
`_rag_index.SingleCollectionRagProvider`; this module is the index's data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from open_pulse_sources.index.github_repos.embed.pipeline import (
    GITHUB_REPOS_COLLECTION,
)

from git_metadata_extractor.providers._rag_index import (
    RagIndexSpec,
    SingleCollectionRagProvider,
    build_provider,
)

if TYPE_CHECKING:
    from open_pulse_sources.index.github_repos.config import GitHubIndexConfig


def _rerank_text(payload: dict[str, Any]) -> str:
    """Build the document fed to the cross-encoder reranker.

    The full README isn't in the payload (it's chunked across many points);
    `repo_id + description` is what we have on every hit and gives the
    reranker enough signal to compare candidates.
    """
    repo_id = payload.get("repo_id") or ""
    desc = payload.get("description") or ""
    return f"{repo_id}\n{desc}".strip() or repo_id


SPEC = RagIndexSpec(
    label="github_rag",
    collection=GITHUB_REPOS_COLLECTION,
    config_module="open_pulse_sources.index.github_repos.config",
    env_var="V2_GITHUB_RAG_ENABLED",
    allowed_filter_keys=frozenset(
        {
            # `entity_type` is always "repos" today; kept for federated symmetry.
            "entity_type",
            "repo_id",
            "owner",
            "primary_language",
            "license_spdx",
            "is_archived",
            "is_fork",
        },
    ),
    thin_keys=(
        "repo_id",
        "owner",
        "name",
        "primary_language",
        "license_spdx",
        "stars",
        "forks",
        "pushed_at",
        "is_archived",
    ),
    rerank_text=_rerank_text,
)


class GitHubRagProvider(SingleCollectionRagProvider):
    """Async wrapper around the GitHub Qdrant index."""

    SPEC: ClassVar[RagIndexSpec] = SPEC


def build_default_provider(
    cfg: GitHubIndexConfig | None = None,
) -> GitHubRagProvider | None:
    return build_provider(GitHubRagProvider, cfg)  # type: ignore[return-value]


__all__ = ["GitHubRagProvider", "build_default_provider"]
