"""Bounded, ingest-only thread pool (Bug 04 — ingest/extraction load isolation).

Bulk index ingestion and interactive extraction run in the same process. Both
offload their blocking work with ``asyncio.to_thread``, which dispatches to the
event loop's *default* ``ThreadPoolExecutor``. A burst of ingest work therefore
saturates that shared pool and extraction's offloaded calls queue behind it,
collapsing extraction throughput.

This module gives the heavy ingest steps (the per-provider embed pass, WAL
checkpoint, and read-only snapshot, plus the gitlab ingest/embed) their own
*bounded* pool, so they can never hold more than ``V2_INGEST_MAX_THREADS``
threads and the default pool stays free for extraction. ``run_in_ingest_pool``
is a drop-in for ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

LOGGER = logging.getLogger(__name__)

_ENV_VAR = "V2_INGEST_MAX_THREADS"
_DEFAULT_MAX_THREADS = 2

_T = TypeVar("_T")
_executor: ThreadPoolExecutor | None = None


def _max_threads() -> int:
    raw = os.getenv(_ENV_VAR)
    if raw is None or not raw.strip():
        return _DEFAULT_MAX_THREADS
    try:
        return max(1, int(raw.strip()))
    except ValueError:
        LOGGER.warning("invalid %s=%r; falling back to %d", _ENV_VAR, raw, _DEFAULT_MAX_THREADS)
        return _DEFAULT_MAX_THREADS


def ingest_executor() -> ThreadPoolExecutor:
    """The process-wide bounded ingest pool (created lazily, on first use)."""
    global _executor  # noqa: PLW0603 — module-level lazy singleton
    if _executor is None:
        workers = _max_threads()
        _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gme-ingest")
        LOGGER.info("ingest thread pool created (max_workers=%d)", workers)
    return _executor


async def run_in_ingest_pool(func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    """Drop-in for ``asyncio.to_thread`` that runs ``func`` on the bounded
    ingest pool instead of the shared default pool, so ingest can't starve
    extraction (Bug 04)."""
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(ingest_executor(), call)


def reset_ingest_pool() -> None:
    """Tear down the pool so the next call rebuilds it (e.g. to pick up a new
    ``V2_INGEST_MAX_THREADS``). Intended for tests."""
    global _executor  # noqa: PLW0603 — module-level lazy singleton
    if _executor is not None:
        _executor.shutdown(wait=False)
        _executor = None
