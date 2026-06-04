"""Tests for the federated store manifest export."""

from __future__ import annotations

from src.index._federated.manifest import (
    build_manifest,
    manifest_entry,
)


class _BareAdapter:
    """Adapter that declares none of the optional manifest attrs."""

    name = "bare_store"
    entity_types = ["thing"]


class _DuckDBSourceAdapter:
    name = "duck_store"
    entity_types = ["community"]
    backend = "duckdb"
    surface_as_source = True
    id_shape = "url"


def test_entry_defaults_for_bare_adapter() -> None:
    e = manifest_entry(_BareAdapter())
    assert e == {
        "name": "bare_store",
        "duckdb": "bare_store.duckdb",
        "entity_types": ["thing"],
        "backend": "vector",          # default
        "surface_as_source": False,   # default
        "id_shape": "url",            # default
    }


def test_entry_reads_declared_attrs() -> None:
    e = manifest_entry(_DuckDBSourceAdapter())
    assert e["backend"] == "duckdb"
    assert e["surface_as_source"] is True
    assert e["duckdb"] == "duck_store.duckdb"


def test_build_manifest_shape_and_sorted() -> None:
    entries = build_manifest()
    assert entries, "expected at least one registered store"
    names = [e["name"] for e in entries]
    assert names == sorted(names)
    required = {"name", "duckdb", "entity_types", "backend", "surface_as_source", "id_shape"}
    for e in entries:
        assert required <= e.keys()
        assert e["duckdb"] == f"{e['name']}.duckdb"


def test_zenodo_communities_is_a_duckdb_source_tile() -> None:
    by_name = {e["name"]: e for e in build_manifest()}
    zc = by_name["zenodo_communities"]
    assert zc["backend"] == "duckdb"
    assert zc["surface_as_source"] is True
    assert zc["entity_types"] == ["community"]


def test_sources_filter_includes_vector_and_allowlisted_duckdb() -> None:
    sources = {e["name"]: e for e in build_manifest(sources_only=True)}
    # DuckDB-only but allowlisted → present.
    assert "zenodo_communities" in sources
    # Vector-backed stores → present by default.
    assert "openalex" in sources
    # Every emitted source is either vector or explicitly allowlisted.
    for e in sources.values():
        assert e["backend"] == "vector" or e["surface_as_source"]
