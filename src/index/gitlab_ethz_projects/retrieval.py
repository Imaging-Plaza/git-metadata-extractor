"""Semantic search over the gitlab_ethz_projects Qdrant collection."""

from __future__ import annotations

from typing import Any

from src.index._gitlab_base.project_retrieval import project_semantic_search
from src.index.gitlab_ethz_projects.config import load_config
from src.index.gitlab_ethz_projects.store import open_store


def search(
    query: str,
    *,
    top_k: int = 10,
    candidate_k: int = 50,
    filter_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Semantically search gitlab_ethz_projects."""
    cfg = load_config()
    store = open_store()
    try:
        return project_semantic_search(
            config=cfg,
            collection=cfg.gitlab.collection,
            store=store,
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            filter_payload=filter_payload,
        )
    finally:
        store.close()
