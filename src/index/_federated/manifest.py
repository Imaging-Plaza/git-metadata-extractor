"""Machine-readable manifest of the federated index stores.

The single contract the Hub (and any other consumer) builds against, so it
never has to infer a store's shape from its name or filename. For every
registered adapter it emits:

    name              the registry key == the DuckDB filename stem
    duckdb            "<name>.duckdb" — the on-disk store file
    entity_types      the entity types the store serves
    backend           "vector" (own Qdrant collection) | "duckdb" (SQL only)
    surface_as_source whether the Hub should show it as a "Sources" tile
    id_shape          shape of the canonical id ("url" in v3.0.0)

Naming is intentionally NOT parsed: some stores are `<source>_<entity>`
(`github_repos`, `zenodo_communities`) and some are bare `<source>` holding
many tables (`openalex`, `snsf`). The store name is opaque; the manifest
carries the structured truth instead.

Usage:
    python -m src.index._federated.manifest            # JSON to stdout
    python -m src.index._federated.manifest --sources  # only Source tiles
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from src.index._federated.registry import IndexAdapter, load_adapters

# Defaults for the optional, declarative adapter attributes (see
# `IndexAdapter` docstring). Read via getattr so adapters opt in only where
# they differ from the default.
_DEFAULT_BACKEND = "vector"
_DEFAULT_SURFACE = False
_DEFAULT_ID_SHAPE = "url"


def manifest_entry(adapter: IndexAdapter) -> dict[str, Any]:
    """Build one manifest entry from a registered adapter."""
    name = adapter.name
    return {
        "name": name,
        "duckdb": f"{name}.duckdb",
        "entity_types": list(adapter.entity_types),
        "backend": getattr(adapter, "backend", _DEFAULT_BACKEND),
        "surface_as_source": bool(
            getattr(adapter, "surface_as_source", _DEFAULT_SURFACE),
        ),
        "id_shape": getattr(adapter, "id_shape", _DEFAULT_ID_SHAPE),
        "structured_query": bool(getattr(adapter, "structured_query", False)),
    }


def build_manifest(*, sources_only: bool = False) -> list[dict[str, Any]]:
    """Return the manifest for all registered stores, sorted by name.

    ``sources_only`` keeps just the stores the Hub should tile under
    "Sources" — i.e. vector-backed stores plus DuckDB-only stores explicitly
    allowlisted via ``surface_as_source``.
    """
    entries = [manifest_entry(a) for a in load_adapters()]
    if sources_only:
        entries = [
            e for e in entries
            if e["surface_as_source"] or e["backend"] == "vector"
        ]
    entries.sort(key=lambda e: e["name"])
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources",
        action="store_true",
        help="emit only stores that should appear as Hub 'Sources' tiles",
    )
    args = parser.parse_args(argv)
    print(json.dumps(build_manifest(sources_only=args.sources), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
