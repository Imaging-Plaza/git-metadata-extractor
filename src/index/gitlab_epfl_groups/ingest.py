"""Ingest public groups from gitlab.epfl.ch into the local DuckDB store."""

from __future__ import annotations

from src.index._gitlab_base.client import GitLabClient
from src.index._gitlab_base.group_ingest import ingest_groups
from src.index.gitlab_epfl_groups.config import load_config
from src.index.gitlab_epfl_groups.store import open_store


def run_ingest(*, limit: int | None = None) -> dict[str, int]:
    """Fetch public groups from gitlab.epfl.ch and upsert into DuckDB."""
    cfg = load_config()
    client = GitLabClient(host=cfg.gitlab.host, token=cfg.gitlab.token)
    store = open_store()
    try:
        return ingest_groups(
            host=cfg.gitlab.host,
            client=client,
            store=store,
            limit=limit,
        )
    finally:
        client.close()
        store.close()
