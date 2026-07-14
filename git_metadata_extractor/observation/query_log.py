"""Per-request log of external-service queries an extraction tries.

Each ``/extract`` call gets a `QueryLog` instance stamped onto the
`query_log_var` `ContextVar`. Tool wrappers (infoscience search, duckduckgo
search, orcid lookup, selenium fetch, ror search, github org metadata,
organization identity, …) call `record_query(service=..., query=...)` before
hitting the network. The agent that owns the tool call is taken from
`current_agent_var` / `current_agent_context_var`, which each agent stamps at
the top of its `run()` via the `current_agent(...)` context manager.

At request completion `api.py` writes the accumulated log to a JSON file
under `V2_QUERY_LOG_DIR` (default ``logs/v2_queries/``).

The module is no-op-safe: when no QueryLog is in the current context (e.g.
the agents are exercised from a test harness without going through
`extract()`), `record_query()` and `current_agent` simply do nothing.
"""
from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_QUERY_LOG_DIR = Path("logs/v2_queries")
QUERY_LOG_DIR_ENV_VAR = "V2_QUERY_LOG_DIR"


@dataclass(slots=True)
class _AgentEntry:
    agent: str
    context: dict[str, Any]
    queries: list[dict[str, Any]] = field(default_factory=list)


class QueryLog:
    """Thread-safe accumulator for one extraction's tool-query history."""

    def __init__(self, run_id: str, extract_full_path: str) -> None:
        self.run_id = run_id
        self.extract_full_path = extract_full_path
        self._lock = Lock()
        # Key: (agent_name, json-serialised context). Each unique pair gets one
        # entry whose `queries` list grows as new tool calls happen.
        self._entries: dict[tuple[str, str], _AgentEntry] = {}

    def record(
        self,
        *,
        service: str,
        query: str,
        agent: str,
        agent_context: dict[str, Any],
    ) -> None:
        ctx_key = json.dumps(agent_context, sort_keys=True, default=str)
        key = (agent, ctx_key)
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _AgentEntry(agent=agent, context=dict(agent_context))
                self._entries[key] = entry
            entry.queries.append(
                {
                    "service": service,
                    "query": query,
                    "ts": timestamp,
                },
            )

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id,
                "extract_full_path": self.extract_full_path,
                "agents": [
                    {
                        "agent": entry.agent,
                        "context": entry.context,
                        "queries": list(entry.queries),
                    }
                    for entry in self._entries.values()
                ],
            }

    def write(self, output_dir: Path | None = None) -> Path | None:
        """Persist the log to ``<output_dir>/<run_id>.json``.

        Returns the written path on success, or `None` on failure (logged).
        Empty logs (no queries recorded) are still written so callers know
        the extraction happened.
        """
        directory = output_dir or _resolve_default_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("failed to create query log directory %s", directory)
            return None
        path = directory / f"{self.run_id}.json"
        try:
            path.write_text(
                json.dumps(self.to_dict(), indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("failed to write query log %s", path)
            return None
        return path


query_log_var: ContextVar[QueryLog | None] = ContextVar("v2_query_log", default=None)
current_agent_var: ContextVar[str | None] = ContextVar(
    "v2_current_agent",
    default=None,
)
current_agent_context_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "v2_current_agent_context",
    default=None,
)


def _resolve_default_dir() -> Path:
    raw = os.getenv(QUERY_LOG_DIR_ENV_VAR)
    if isinstance(raw, str) and raw.strip():
        return Path(raw.strip())
    return DEFAULT_QUERY_LOG_DIR


def stamp_current_agent(*, name: str, context: dict[str, Any] | None = None) -> None:
    """Set the active agent name and context for the current async task.

    Unlike `current_agent` (the context manager), this helper does NOT restore
    previous values on exit — it's intended for the top of an agent's `run()`
    method where the agent owns the rest of the task's context. ContextVars
    set inside an asyncio task die when the task completes.
    """
    current_agent_var.set(name)
    current_agent_context_var.set(dict(context) if context else {})


def record_query(*, service: str, query: str) -> None:
    """Record a tool-issued external-service query, if a QueryLog is active.

    Service is namespaced per provider/method (e.g.
    ``infoscience.search_person``); query is the raw user input the LLM passed.
    Caller doesn't need to know whether a log is active or which agent is
    running — both are read from ContextVars and may be missing.
    """
    log = query_log_var.get()
    if log is None:
        return
    log.record(
        service=service,
        query=query,
        agent=current_agent_var.get() or "unknown",
        agent_context=current_agent_context_var.get() or {},
    )


class current_agent:
    """Context manager: stamp the active agent name + context onto the ContextVars.

    Use inside an agent's ``run()`` so any tool call made downstream is
    attributed to that agent in the query log. Restores the previous values
    on exit so nested invocations behave correctly.
    """

    def __init__(self, *, name: str, context: dict[str, Any] | None = None) -> None:
        self._name = name
        self._context = dict(context) if context else {}
        self._prev_name: str | None = None
        self._prev_context: dict[str, Any] | None = None

    def __enter__(self) -> "current_agent":
        self._prev_name = current_agent_var.get()
        self._prev_context = current_agent_context_var.get()
        current_agent_var.set(self._name)
        current_agent_context_var.set(self._context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        current_agent_var.set(self._prev_name)
        current_agent_context_var.set(self._prev_context)
        return False


__all__ = [
    "DEFAULT_QUERY_LOG_DIR",
    "QUERY_LOG_DIR_ENV_VAR",
    "QueryLog",
    "current_agent",
    "current_agent_context_var",
    "current_agent_var",
    "query_log_var",
    "record_query",
    "stamp_current_agent",
]
