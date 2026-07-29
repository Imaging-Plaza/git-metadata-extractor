from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from git_metadata_extractor.agents.llm._verdict_cache import (
    get_cached_agent_verdict,
    store_agent_verdict,
)
from git_metadata_extractor.agents.models import AgentResult
from git_metadata_extractor.providers.cache import ProviderCache


def _result() -> AgentResult:
    return AgentResult(
        data={"id": "https://github.com/cmdoret", "schema:name": "C M"},
        warnings=["sample warning"],
        raw_output={"foo": "bar"},
        is_partial=False,
        failure_reason=None,
        model="openai/gpt-test",
        provider="openai",
        tokens_prompt=100,
        tokens_completion=50,
        stats={"agent_runtime": "llm"},
    )


def test_no_cache_returns_none(tmp_path: Path) -> None:
    assert (
        get_cached_agent_verdict(
            None,
            agent_name="person",
            identity={"primary_key": "username", "value": "cmdoret"},
        )
        is None
    )


def test_no_identity_returns_none(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    assert get_cached_agent_verdict(cache, agent_name="person", identity=None) is None


def test_partial_results_are_not_cached(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    partial = AgentResult(data={}, warnings=["bad"], is_partial=True)
    identity = {"primary_key": "username", "value": "cmdoret"}
    store_agent_verdict(cache, agent_name="person", identity=identity, result=partial)
    assert get_cached_agent_verdict(cache, agent_name="person", identity=identity) is None


def test_round_trip_returns_cached_data_with_cached_flag(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    result = _result()
    identity = {"primary_key": "username", "value": "cmdoret"}
    store_agent_verdict(cache, agent_name="person", identity=identity, result=result)

    hit = get_cached_agent_verdict(cache, agent_name="person", identity=identity)
    assert hit is not None
    assert hit.data == result.data
    assert hit.warnings == ["sample warning"]
    assert hit.raw_output == {"foo": "bar"}
    # Token/model fields are reset on retrieval.
    assert hit.model is None
    assert hit.provider is None
    assert hit.tokens_prompt == 0
    assert hit.tokens_completion == 0
    # Stats round-trip plus a cached marker.
    assert hit.stats["agent_runtime"] == "llm"
    assert hit.stats["cached"] is True


def test_different_identity_misses(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    result = _result()
    store_agent_verdict(
        cache,
        agent_name="person",
        identity={"primary_key": "username", "value": "cmdoret"},
        result=result,
    )
    miss = get_cached_agent_verdict(
        cache,
        agent_name="person",
        identity={"primary_key": "username", "value": "someone-else"},
    )
    assert miss is None


def test_different_agent_name_misses(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    result = _result()
    identity = {"primary_key": "username", "value": "cmdoret"}
    store_agent_verdict(cache, agent_name="person", identity=identity, result=result)
    miss = get_cached_agent_verdict(cache, agent_name="organization", identity=identity)
    assert miss is None


def test_is_root_skips_lookup_even_when_cached(tmp_path: Path) -> None:
    cache = ProviderCache(tmp_path / "p.db")
    result = _result()
    identity = {"primary_key": "username", "value": "cmdoret"}
    # Pre-populate the cache.
    store_agent_verdict(cache, agent_name="person", identity=identity, result=result)
    # A normal fan-out call would hit.
    assert get_cached_agent_verdict(cache, agent_name="person", identity=identity) is not None
    # The same lookup with is_root=True must miss.
    assert (
        get_cached_agent_verdict(
            cache,
            agent_name="person",
            identity=identity,
            is_root=True,
        )
        is None
    )


def test_root_writes_are_still_persisted(tmp_path: Path) -> None:
    """A root extraction skips reads but the result still lands in the cache."""
    cache = ProviderCache(tmp_path / "p.db")
    identity = {"primary_key": "username", "value": "cmdoret"}
    # Simulate a root run: we never look up, but we still store on success.
    assert (
        get_cached_agent_verdict(
            cache,
            agent_name="person",
            identity=identity,
            is_root=True,
        )
        is None
    )
    store_agent_verdict(cache, agent_name="person", identity=identity, result=_result())
    # A subsequent fan-out call should now find it.
    hit = get_cached_agent_verdict(cache, agent_name="person", identity=identity)
    assert hit is not None
    assert hit.stats["cached"] is True
