"""Ingest public users from gitlab.ethz.ch into the local DuckDB store."""

from __future__ import annotations

from src.index._gitlab_base.client import GitLabClient
from src.index._gitlab_base.user_ingest import ingest_users
from src.index.gitlab_ethz_users.config import load_config
from src.index.gitlab_ethz_users.store import open_store


def run_ingest(*, limit: int | None = None) -> dict[str, int]:
    """Fetch public users from gitlab.ethz.ch and upsert into DuckDB."""
    cfg = load_config()
    client = GitLabClient(host=cfg.gitlab.host, token=cfg.gitlab.token)
    store = open_store()
    try:
        return ingest_users(
            host=cfg.gitlab.host,
            client=client,
            store=store,
            limit=limit,
        )
    finally:
        client.close()
        store.close()
