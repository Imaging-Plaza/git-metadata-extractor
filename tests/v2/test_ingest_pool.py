# tests/v2/test_ingest_pool.py
"""Bug 04: the bounded ingest pool keeps bulk ingestion off the default thread
pool that extraction relies on, so ingest can't starve extraction.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from src.v2.indices._ingest_pool import (
    reset_ingest_pool,
    run_in_ingest_pool,
)


@pytest.fixture(autouse=True)
def _fresh_pool():
    reset_ingest_pool()
    yield
    reset_ingest_pool()


def test_runs_on_dedicated_named_thread():
    name = asyncio.run(run_in_ingest_pool(lambda: threading.current_thread().name))
    assert name.startswith("gme-ingest")


def test_returns_result_and_forwards_args_kwargs():
    assert asyncio.run(run_in_ingest_pool(lambda a, b: a + b, 2, b=3)) == 5


def test_bounded_concurrency(monkeypatch):
    monkeypatch.setenv("V2_INGEST_MAX_THREADS", "2")
    reset_ingest_pool()
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    def work() -> None:
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1

    async def go():
        await asyncio.gather(*(run_in_ingest_pool(work) for _ in range(6)))

    asyncio.run(go())
    assert state["peak"] <= 2  # never exceeds the configured cap


def test_saturated_ingest_pool_does_not_block_extraction_offload(monkeypatch):
    # The core isolation property: a fully-occupied ingest pool must not delay
    # an extraction-path asyncio.to_thread call (which uses the default pool).
    monkeypatch.setenv("V2_INGEST_MAX_THREADS", "1")
    reset_ingest_pool()
    release = threading.Event()

    async def go():
        blocked = asyncio.create_task(run_in_ingest_pool(release.wait))
        await asyncio.sleep(0.05)  # let it occupy the single ingest thread
        try:
            # default-pool offload must still complete promptly despite the
            # ingest pool being saturated
            return await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), timeout=2.0)
        finally:
            release.set()
            await blocked

    assert asyncio.run(go()) == "ok"
