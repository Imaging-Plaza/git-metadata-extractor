"""run_embed_step publishes a read-only snapshot after ingest+checkpoint.

Verifies the wiring that lets the Hub read each v2-ingest provider's data
without contending on the live DuckDB write lock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import duckdb

from src.index._snapshot import snapshot_path_for
from src.index.dockerhub.models import DockerhubRepoRecord
from src.index.dockerhub.storage.duckdb_store import DockerhubStore
from src.v2.indices._embed_step import run_embed_step


def test_run_embed_step_publishes_snapshot(tmp_path: Path) -> None:
    store = DockerhubStore.open(tmp_path / "dockerhub.duckdb")
    store.upsert_image(
        DockerhubRepoRecord(
            repo_id="library/python", namespace="library", name="python",
            pull_count=99,
        ),
    )

    out = asyncio.run(
        run_embed_step(
            provider="dockerhub",
            job_id="j1",
            embed_call=lambda: {"images": 0},  # no-op embed
            checkpoint_store=store,
        ),
    )

    assert out["snapshot"]["published"] is True
    snap = snapshot_path_for(store.db_path)
    assert snap.exists()

    ro = duckdb.connect(str(snap), read_only=True)
    assert ro.execute("SELECT count(*) FROM images").fetchone()[0] == 1
    # chunks (embedding bookkeeping) excluded from the served snapshot
    assert ro.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='chunks'",
    ).fetchone()[0] == 0
    ro.close()
    store.close()


def test_run_embed_step_snapshot_block_present_without_store() -> None:
    """No checkpoint_store → snapshot block reports it cleanly, no crash."""
    out = asyncio.run(
        run_embed_step(
            provider="x", job_id="j", embed_call=lambda: {"n": 0},
        ),
    )
    assert out["snapshot"]["ran"] is False
