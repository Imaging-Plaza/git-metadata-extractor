"""Embed gitlab_ethz_projects into Qdrant via the RCP embeddings endpoint."""

from __future__ import annotations

from src.index._gitlab_base.project_embed import embed_projects
from src.index.gitlab_ethz_projects.config import load_config
from src.index.gitlab_ethz_projects.store import open_store


def run_embed(*, limit: int | None = None) -> dict[str, int]:
    """Chunk and embed un-embedded gitlab_ethz_projects into Qdrant."""
    cfg = load_config()
    store = open_store()
    try:
        return embed_projects(
            config=cfg,
            store=store,
            collection=cfg.gitlab.collection,
            limit=limit,
        )
    finally:
        store.close()
