"""Smoke tests for the github_users / github_organizations /
huggingface_papers federated adapters.

Asserts each adapter registers with the federated registry under the
right name, declares the right entity types, and is reachable through
the `import_all_adapters` discovery path. Actual DuckDB-backed search
is exercised in the per-module unit tests — here we only verify the
plumbing.
"""

from __future__ import annotations

from src.index._federated.adapters.github_organizations import (
    GitHubOrganizationsAdapter,
)
from src.index._federated.adapters.github_users import GitHubUsersAdapter
from src.index._federated.adapters.huggingface_papers import (
    HuggingFacePapersAdapter,
)
from src.index._federated.registry import REGISTRY, load_adapters


def test_github_users_adapter_registers_under_module_name() -> None:
    adapter = GitHubUsersAdapter()
    assert adapter.name == "github_users"
    assert adapter.entity_types == ["user"]
    assert callable(adapter.search)
    assert callable(adapter.lookup)


def test_github_organizations_adapter_registers_under_module_name() -> None:
    adapter = GitHubOrganizationsAdapter()
    assert adapter.name == "github_organizations"
    assert adapter.entity_types == ["organization"]
    assert callable(adapter.search)
    assert callable(adapter.lookup)


def test_huggingface_papers_adapter_registers_under_module_name() -> None:
    adapter = HuggingFacePapersAdapter()
    assert adapter.name == "huggingface_papers"
    assert adapter.entity_types == ["paper"]
    assert callable(adapter.search)
    assert callable(adapter.lookup)


def test_load_adapters_discovers_the_three_new_modules() -> None:
    """The registry's candidate list must include the new adapter
    module names so a vanilla `gme federated ...` call picks them up
    alongside the pre-existing 13."""
    load_adapters()
    assert "github_users" in REGISTRY
    assert "github_organizations" in REGISTRY
    assert "huggingface_papers" in REGISTRY


def test_new_adapters_return_empty_on_missing_backing_store(tmp_path) -> None:
    """Without an underlying DuckDB file on disk, lookup/search both
    return [] cleanly rather than raising — same contract as the
    other adapters."""
    import os

    # Point INDEX_DATA_DIR somewhere empty so no `.duckdb` files exist.
    old = os.environ.get("INDEX_DATA_DIR")
    os.environ["INDEX_DATA_DIR"] = str(tmp_path)
    try:
        gu = GitHubUsersAdapter()
        go = GitHubOrganizationsAdapter()
        hp = HuggingFacePapersAdapter()

        # search() should swallow any config / file errors and return [].
        assert gu.search(query="anything", entity_type=None, top_k=5, filters=None) == []
        assert go.search(query="anything", entity_type=None, top_k=5, filters=None) == []
        assert hp.search(query="anything", entity_type=None, top_k=5, filters=None) == []

        # lookup() should likewise return [] for missing-store cases.
        # github_users / github_organizations: empty DB → no row → [].
        assert gu.lookup("octocat") == []
        assert go.lookup("EPFL-ENAC") == []
        # huggingface_papers: arxiv-id-shaped input → no row → [].
        assert hp.lookup("2310.01234") == []
        # huggingface_papers: non-arxiv input → normaliser rejects → [].
        assert hp.lookup("not-an-arxiv-id") == []
    finally:
        if old is None:
            os.environ.pop("INDEX_DATA_DIR", None)
        else:
            os.environ["INDEX_DATA_DIR"] = old
