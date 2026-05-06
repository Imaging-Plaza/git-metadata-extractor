"""Request-scoped logging context for v2.

Adds a `[repo-<pid>-<rand>]`-style request-id prefix to every log record
emitted while inside an async request handler. Implementation mirrors the
behaviour of the v1 helper (`src.v1.utils.enhanced_logging`) so logs from
both v1 and v2 modules share the same prefix when they run inside the same
request context.

Public surface:

- `setup_logging(level=..., use_colors=True)` — install a colored stdout
  handler with the request-id formatter on the root logger and silence the
  noisy third-party libraries we don't want in normal output.
- `AsyncRequestContext(request_id=..., prefix="repo")` — async context
  manager. Generates a request id (`prefix-<pid>-<4-hex>`), stamps it on
  the active `ContextVar`, restores the previous value on exit.
- `RequestContext` — sync variant of the same.
- `set_request_id`, `get_request_id`, `clear_request_id`,
  `generate_request_id` — direct accessors when a context manager is awkward.

The format string includes `%(request_id)s` (or `[%(request_id)s]` in the
colored variant). When no request id is set, the field renders empty so
out-of-request logs stay clean.
"""
from __future__ import annotations

import logging
import os
import random
import sys
from contextvars import ContextVar
from typing import Optional

# ContextVar that propagates across `await` boundaries inside the same task.
request_id_var: ContextVar[Optional[str]] = ContextVar("v2_request_id", default=None)


class _Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"

    DEBUG = "\033[36m"
    INFO = "\033[32m"
    WARNING = "\033[33m"
    ERROR = "\033[31m"
    CRITICAL = "\033[35m"

    REQUEST_COLORS = (
        "\033[94m",
        "\033[92m",
        "\033[96m",
        "\033[95m",
        "\033[93m",
        "\033[91m",
        "\033[97m",
        "\033[90m",
    )

    @classmethod
    def request_color(cls, request_id: str) -> str:
        if not request_id:
            return cls.RESET
        return cls.REQUEST_COLORS[hash(request_id) % len(cls.REQUEST_COLORS)]


_LEVEL_COLORS = {
    "DEBUG": _Colors.DEBUG,
    "INFO": _Colors.INFO,
    "WARNING": _Colors.WARNING,
    "ERROR": _Colors.ERROR,
    "CRITICAL": _Colors.CRITICAL,
}


class _ColoredFormatter(logging.Formatter):
    """Adds request-id and colors to log output."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = request_id_var.get()
        if request_id:
            record.request_id = f"[{request_id}]"
            record.request_color = _Colors.request_color(request_id)
        else:
            record.request_id = ""
            record.request_color = ""

        levelname = record.levelname
        if levelname in _LEVEL_COLORS:
            record.levelname = f"{_LEVEL_COLORS[levelname]}{levelname}{_Colors.RESET}"

        return super().format(record) + _Colors.RESET


class _RequestContextFilter(logging.Filter):
    """Stamps the current request id onto every record (plain mode)."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = request_id_var.get()
        record.request_id = request_id or ""
        record.request_color = _Colors.request_color(request_id) if request_id else ""
        return True


def generate_request_id(prefix: str = "req") -> str:
    """Return `prefix-<pid>-<4-hex>` (worker-pid-safe)."""
    pid = os.getpid()
    suffix = "".join(random.choices("0123456789abcdef", k=4))
    return f"{prefix}-{pid}-{suffix}"


def set_request_id(request_id: Optional[str] = None, prefix: str = "req") -> str:
    """Stamp `request_id` (or a freshly generated one) onto the ContextVar."""
    if request_id is None:
        request_id = generate_request_id(prefix)
    request_id_var.set(request_id)
    return request_id


def clear_request_id() -> None:
    """Clear the request id from the current context."""
    request_id_var.set(None)


def get_request_id() -> Optional[str]:
    """Return the active request id, or `None` if not in a request context."""
    return request_id_var.get()


def setup_logging(level: int = logging.INFO, *, use_colors: bool = True) -> None:
    """Install the request-id-aware handler on the root logger.

    Pass `use_colors=False` for a plain text format (e.g., for log files
    or non-terminal sinks).
    """
    handler = logging.StreamHandler(sys.stdout)

    if use_colors:
        formatter = _ColoredFormatter(
            fmt=(
                "%(asctime)s %(levelname)s %(request_color)s%(request_id)s"
                "\033[0m %(name)s: %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(request_id)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    handler.addFilter(_RequestContextFilter())

    logging.basicConfig(level=level, handlers=[handler], force=True)

    # Quiet down libraries that log every request/response at DEBUG/INFO.
    for noisy in (
        "rdflib",
        "urllib3",
        "selenium",
        "httpx",
        "httpcore",
        "httpcore.http11",
        "openai",
        "openai._base_client",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class RequestContext:
    """Sync context manager that pins a request id on the active ContextVar.

    Example:
        with RequestContext(prefix="repo") as request_id:
            logger.info("processing")  # logs include `[repo-<pid>-<hex>]`
    """

    def __init__(self, request_id: Optional[str] = None, prefix: str = "req") -> None:
        self.request_id = request_id or generate_request_id(prefix)
        self._previous_id: Optional[str] = None

    def __enter__(self) -> str:
        self._previous_id = request_id_var.get()
        request_id_var.set(self.request_id)
        return self.request_id

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        request_id_var.set(self._previous_id)
        return False


class AsyncRequestContext:
    """Async variant of `RequestContext`. Use as `async with`."""

    def __init__(self, request_id: Optional[str] = None, prefix: str = "req") -> None:
        self.request_id = request_id or generate_request_id(prefix)
        self._previous_id: Optional[str] = None

    async def __aenter__(self) -> str:
        self._previous_id = request_id_var.get()
        request_id_var.set(self.request_id)
        return self.request_id

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        request_id_var.set(self._previous_id)
        return False


__all__ = [
    "AsyncRequestContext",
    "RequestContext",
    "clear_request_id",
    "generate_request_id",
    "get_request_id",
    "request_id_var",
    "set_request_id",
    "setup_logging",
]
