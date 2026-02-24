from __future__ import annotations

import functools
import sqlite3
import time
from typing import Callable, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")

_SQLITE_BUSY_ERROR_CODES = {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}


class GraphStoreBusyError(RuntimeError):
    """Raised when SQLite remains busy after retry attempts."""


def _is_sqlite_busy_error(error: sqlite3.OperationalError) -> bool:
    sqlite_error_code = getattr(error, "sqlite_errorcode", None)
    if sqlite_error_code in _SQLITE_BUSY_ERROR_CODES:
        return True

    normalized_message = str(error).strip().lower()
    busy_markers = ("database is locked", "database is busy", "sqlite_busy")
    return any(marker in normalized_message for marker in busy_markers)


def with_write_retry(
    max_retries: int = 3,
    backoff_base: float = 0.1,
):
    """Retry SQLite write operations that fail with transient busy/locked errors."""
    if max_retries < 0:
        message = f"max_retries must be >= 0, got {max_retries}"
        raise ValueError(message)
    if backoff_base < 0:
        message = f"backoff_base must be >= 0, got {backoff_base}"
        raise ValueError(message)

    def _decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def _wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            attempts = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as error:  # noqa: PERF203
                    if not _is_sqlite_busy_error(error):
                        raise
                    if attempts >= max_retries:
                        message = (
                            "SQLite write remained busy after "
                            f"{max_retries + 1} attempts for '{func.__name__}'"
                        )
                        raise GraphStoreBusyError(message) from error

                    sleep_seconds = backoff_base * (2**attempts)
                    time.sleep(sleep_seconds)
                    attempts += 1

        return _wrapped

    return _decorator
