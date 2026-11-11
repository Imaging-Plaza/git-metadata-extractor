"""Shared helpers for caching LLM agent verdicts in `ProviderCache`.

Each agent picks a stable identity slice from its incoming context and uses
these helpers to skip the LLM call when the same identity has already been
processed within the cache TTL.

Cached entries store only JSON-serialisable parts (`data`, `warnings`,
`raw_output`, `stats`); model name, provider, and token counts are reset on
retrieval, and `stats["cached"] = True` is added so observability can tell.

Root extractions never consume the cache: when an extraction request targets
an entity directly (e.g., `/extract/.../user/cmdoret`), the root agent runs
fresh against the full root context. Pass `is_root=True` to
`get_cached_agent_verdict` to disable the lookup. The store call still runs
on a successful root completion so the result is available to later fan-out
invocations of the same identity. The orchestrator sets
`context["agent_is_root"] = True` on root stages; agents forward that flag
through here.
"""
from __future__ import annotations

import logging
from typing import Any

from git_metadata_extractor.agents.models import AgentResult
from git_metadata_extractor.agents.tool_catalog import current_tool_fingerprint
from git_metadata_extractor.mapping import mapping_manifest
from git_metadata_extractor.observation.evidence import get_trace
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.sources_service import current_sources_fingerprint

logger = logging.getLogger(__name__)


def get_cached_agent_verdict(
    cache: ProviderCache | None,
    *,
    agent_name: str,
    identity: dict[str, Any] | None,
    is_root: bool = False,
) -> AgentResult | None:
    """Return a cached `AgentResult` if available, else `None`.

    Root extractions never hit the cache — see module docstring.
    """
    if cache is None or not identity or is_root:
        return None
    key = _verdict_key(agent_name, identity)
    cached = cache.get(key)
    if not isinstance(cached, dict):
        return None
    payload = cached.get("data")
    if not isinstance(payload, dict):
        return None
    trace = get_trace()
    if trace is not None:
        packet = cached.get("provenance")
        trace.restore(packet)
        current = trace.activities.get(trace.active_id())
        if current is not None:
            current["cache_hit"] = True
            current["kind"] = "cache_reuse"
            current["reused"] = list((packet or {}).get("activities", {}))
    logger.info(
        "agent cache hit: %s identity=%s",
        agent_name,
        identity,
    )
    return AgentResult(
        data=dict(payload),
        warnings=list(cached.get("warnings", []) or []),
        raw_output=dict(cached.get("raw_output", {}) or {}),
        is_partial=False,
        failure_reason=None,
        model=None,
        provider=None,
        tokens_prompt=0,
        tokens_completion=0,
        stats={**dict(cached.get("stats", {}) or {}), "cached": True},
    )


def store_agent_verdict(
    cache: ProviderCache | None,
    *,
    agent_name: str,
    identity: dict[str, Any] | None,
    result: AgentResult,
) -> None:
    """Cache `result` for future lookups when it is complete and identifiable."""
    if cache is None or not identity or result.is_partial:
        return
    key = _verdict_key(agent_name, identity)
    trace = get_trace()
    if trace is not None:
        trace.observe(result.data)
    cache.set(
        key,
        {
            "data": result.data,
            "warnings": list(result.warnings),
            "raw_output": result.raw_output,
            "stats": result.stats,
            "provenance": trace.snapshot() if trace is not None else None,
        },
    )


def _verdict_key(agent_name: str, identity: dict[str, Any]) -> str:
    fingerprint = current_tool_fingerprint()
    parameters = {**identity, "tool_catalog_fingerprint": fingerprint} if fingerprint else identity
    if backend := current_sources_fingerprint():
        parameters = {**parameters, "sources_backend": backend}
    parameters = {**parameters, "mapping_fingerprint": mapping_manifest()["fingerprint"]}
    return ProviderCache.make_key("agent", agent_name, **parameters)


__all__ = ["get_cached_agent_verdict", "store_agent_verdict"]
