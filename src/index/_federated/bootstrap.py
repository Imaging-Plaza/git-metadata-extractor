"""Bootstrap utility: open every index store's DuckDB to apply schema + migrations.

Running this on a fresh checkout produces structurally-ready (empty) stores so
that manifest, maintenance, and search-against-empty all work without any
network access or API tokens.

Usage
-----
    python -m src.index._federated.bootstrap
    python -m src.index._federated.bootstrap --only dockerhub --only github_repos
    make bootstrap-index
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INDEX_SRC = Path(__file__).resolve().parents[1]  # src/index/
_SKIP_STORES: frozenset[str] = frozenset({"huggingface"})
_LEAF_STORES: frozenset[str] = frozenset(
    {"gitlab_epfl_projects", "gitlab_ethz_projects", "gitlab_datascience_projects"},
)
_DEFAULT_INDEX_DATA_DIR = Path("data/index")


def _index_data_dir() -> Path:
    """Resolve INDEX_DATA_DIR (mirrors the pattern used by each store's paths.py)."""
    raw = os.getenv("INDEX_DATA_DIR", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate
        # relative → anchored at repo root (4 levels up from this file)
        return Path(__file__).resolve().parents[4] / candidate
    return Path(__file__).resolve().parents[4] / _DEFAULT_INDEX_DATA_DIR


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_store_names() -> list[str]:
    """Return sorted list of store names (subdirs of src/index/ that are real stores).

    Excludes:
    - Directories starting with ``_`` or ``__``.
    - Stores listed in ``_SKIP_STORES`` (e.g. ``huggingface`` legacy monolith).
    """
    return [
        d.name
        for d in sorted(_INDEX_SRC.iterdir())
        if d.is_dir()
        and not d.name.startswith("_")
        and d.name not in _SKIP_STORES
    ]


# ---------------------------------------------------------------------------
# Per-store bootstrap
# ---------------------------------------------------------------------------


def bootstrap_store(name: str) -> str:
    """Open (and close) one index store, applying its schema/migrations.

    Returns one of:
    - ``"created"``  — the DuckDB file did not exist before opening.
    - ``"exists"``   — the DuckDB file already existed (idempotent re-run).
    - ``"error: <msg>"`` — caught exception; does NOT propagate.
    - ``"skipped: no duckdb store"`` — store has no recognisable opener.
    """
    try:
        # Determine whether the file already exists before we open it.
        duckdb_path = _index_data_dir() / name / "duckdb" / f"{name}.duckdb"
        existed_before = duckdb_path.exists()

        store = _open_store(name)
        if store is None:
            return "skipped: no duckdb store"

        if hasattr(store, "close"):
            store.close()

    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"
    else:
        return "exists" if existed_before else "created"


def _open_store(name: str) -> object | None:
    """Perform the actual import + open() call for *name*.

    Returns the opened store object, or ``None`` if no recognisable opener
    is found.
    """
    # ------------------------------------------------------------------
    # Leaf stores: open via `src.index.<name>.store.open_store()`
    # ------------------------------------------------------------------
    if name in _LEAF_STORES:
        mod = importlib.import_module(f"src.index.{name}.store")
        open_fn = getattr(mod, "open_store", None)
        if open_fn is not None:
            return open_fn()
        return None

    # ------------------------------------------------------------------
    # Convention stores: `src.index.<name>.storage.duckdb_store.<*Store>`
    # ------------------------------------------------------------------
    try:
        mod = importlib.import_module(f"src.index.{name}.storage.duckdb_store")
    except ModuleNotFoundError:
        return None

    # Find the first class whose name ends in "Store" and is defined in this module.
    store_cls = None
    for _attr_name, obj in inspect.getmembers(mod, inspect.isclass):
        if obj.__name__.endswith("Store") and obj.__module__ == mod.__name__:
            store_cls = obj
            break

    if store_cls is None:
        return None

    return store_cls.open()


# ---------------------------------------------------------------------------
# Bulk bootstrap
# ---------------------------------------------------------------------------


def bootstrap_all(only: list[str] | None = None) -> dict[str, str]:
    """Bootstrap every discovered store and return ``{name: status}`` mapping.

    Parameters
    ----------
    only:
        When provided, only bootstrap stores whose names appear in this list.
        Stores in *only* that are not discovered are silently ignored.
    """
    names = discover_store_names()
    if only is not None:
        only_set = set(only)
        names = [n for n in names if n in only_set]

    return {name: bootstrap_store(name) for name in names}


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap (create + schema-apply) every index DuckDB store.",
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        default=None,
        help="Bootstrap only this store (repeatable). Omit to bootstrap all.",
    )
    args = parser.parse_args(argv)

    result = bootstrap_all(only=args.only)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
