"""Shared runtime helpers for v2 terminal-agent skills.

Skills are CLI tools invoked by an external harness (pi.dev by default).
Output contract:
- Success: a single JSON document on stdout, exit 0.
- Failure: a JSON `{"error": "...", "kind": "..."}` on stderr, non-zero exit.

The harness reads stdout for tool results; stderr surfaces in the
executor's tool-call transcript so the LLM can react to failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SkillError(Exception):
    """Structured error surfaced to the harness on stderr."""

    message: str
    kind: str = "skill_error"

    def __str__(self) -> str:
        return self.message


def emit_success(payload: Any) -> None:
    """Write a single JSON document to stdout and exit 0."""
    json.dump(payload, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def emit_error(err: BaseException, *, kind: str | None = None) -> None:
    """Write a structured error to stderr and exit non-zero."""
    if isinstance(err, SkillError):
        payload = {"error": err.message, "kind": err.kind}
    else:
        payload = {
            "error": str(err) or err.__class__.__name__,
            "kind": kind or err.__class__.__name__,
        }
    json.dump(payload, sys.stderr, ensure_ascii=False, default=str)
    sys.stderr.write("\n")
    sys.stderr.flush()


def run_async_skill(coro_fn: Callable[[], Awaitable[Any]]) -> int:
    """Run an async skill body and translate its outcome into a process exit code.

    Wraps the boilerplate every CLI skill needs: configure logging, run the
    coroutine on a fresh event loop, route success → stdout JSON, errors →
    stderr JSON with a non-zero exit code.
    """
    _configure_logging()
    try:
        result = asyncio.run(coro_fn())
    except SkillError as err:
        emit_error(err)
        return 1
    except KeyboardInterrupt:
        emit_error(SkillError("interrupted", kind="interrupted"))
        return 130
    except Exception as err:  # noqa: BLE001 — final boundary, surface anything
        logger.exception("skill failed")
        emit_error(err)
        return 2
    emit_success(result)
    return 0


def _configure_logging() -> None:
    level_name = os.environ.get("V2_SKILL_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    # Force stderr — stdout is reserved for the JSON result.
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


__all__ = ["SkillError", "emit_error", "emit_success", "run_async_skill"]
