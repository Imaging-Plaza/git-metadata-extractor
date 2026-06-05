"""Embed gitlab_datascience_groups into Qdrant via the RCP embeddings endpoint."""

from __future__ import annotations

from src.index._gitlab_base.group_embed import embed_groups
from src.index.gitlab_datascience_groups.config import load_config
from src.index.gitlab_datascience_groups.store import open_store


def run_embed(*, limit: int | None = None) -> dict[str, int]:
    """Chunk and embed un-embedded gitlab_datascience_groups into Qdrant."""
    cfg = load_config()
    store = open_store()
    try:
        return embed_groups(
            config=cfg,
            store=store,
            collection=cfg.gitlab.collection,
            limit=limit,
        )
    finally:
        store.close()
