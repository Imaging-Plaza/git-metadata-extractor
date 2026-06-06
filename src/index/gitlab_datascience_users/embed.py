"""Embed gitlab_datascience_users into Qdrant via the RCP embeddings endpoint."""

from __future__ import annotations

from src.index._gitlab_base.user_embed import embed_users
from src.index.gitlab_datascience_users.config import load_config
from src.index.gitlab_datascience_users.store import open_store


def run_embed(*, limit: int | None = None) -> dict[str, int]:
    """Chunk and embed un-embedded gitlab_datascience_users into Qdrant."""
    cfg = load_config()
    store = open_store()
    try:
        return embed_users(
            config=cfg,
            store=store,
            collection=cfg.gitlab.collection,
            limit=limit,
        )
    finally:
        store.close()
