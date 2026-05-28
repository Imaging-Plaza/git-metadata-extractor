"""Scope resolution + seed loading."""

from __future__ import annotations

from src.index.huggingface.ingest.scope import resolve_scope


def test_resolve_epfl_scope(base_config):
    scope = resolve_scope("epfl", base_config)
    assert scope.name == "epfl"
    assert "epfl-llm" in scope.seeds


def test_resolve_switzerland_scope(base_config):
    scope = resolve_scope("switzerland", base_config)
    assert scope.name == "switzerland"
    assert "swiss-ai" in scope.seeds
    # Switzerland seed must be a strict superset of the EPFL seed.
    epfl = set(resolve_scope("epfl", base_config).seeds)
    assert epfl.issubset(set(scope.seeds))
