"""Cross-store DuckDB maintenance: checkpoint + compact + refresh `.ro` snapshot.

Walks every on-disk index store under ``$INDEX_DATA_DIR`` (default
``data/index``) and, per store, runs the shared
:func:`src.index._snapshot.publish_snapshot` through a live writer connection:
``CHECKPOINT`` (fold the WAL into the live file) + rewrite every served table
into a fresh, compacted ``<store>.ro.duckdb`` — the read-only file the Hub
reads. ``--check`` reports rows + file sizes + snapshot staleness without
writing anything.

Enumeration is by **disk**, not the federated registry, so it covers *every*
store on disk — including ones without a federated adapter (e.g. the per-entity
HuggingFace stores) and excluding FAISS-only stores (e.g. `ror`) that have no
DuckDB file.

Scope: this refreshes the compacted `.ro` snapshot and folds the live WAL. It
deliberately does **not** deep-compact the *live* file — DuckDB has no safe
in-place VACUUM, and a CTAS rewrite would drop PK/constraints; the `.ro` copy
is the compacted artifact consumers read.

Requires exclusive write access per store: run when the serving process is not
holding that store's live file open.

Usage:
    python -m src.index._federated.maintenance            # optimize every store
    python -m src.index._federated.maintenance --store snsf
    python -m src.index._federated.maintenance --check    # report only, no writes
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def index_data_root() -> Path:
    """The index data root — mirrors each store's ``paths.index_data_root()``."""
    return Path(os.getenv("INDEX_DATA_DIR", "data/index")).expanduser().resolve()


def discover_stores(only: str | None = None) -> list[tuple[str, Path]]:
    """Return ``(store_name, live_duckdb_path)`` for every on-disk store.

    Live layout is ``<root>/<store>/duckdb/<store>.duckdb``; a flat
    ``<root>/<store>/<store>.duckdb`` is also accepted. ``.ro``/``.tmp`` files
    are skipped. Sorted by name; deduped by resolved path.
    """
    root = index_data_root()
    seen: set[Path] = set()
    out: list[tuple[str, Path]] = []
    for pattern in ("*/duckdb/*.duckdb", "*/*.duckdb"):
        for p in sorted(root.glob(pattern)):
            name = p.name
            if name.endswith(".ro.duckdb") or name.endswith(".tmp"):
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            store = p.stem
            if only and store != only:
                continue
            out.append((store, p))
    out.sort(key=lambda t: t[0])
    return out


def _file_bytes(p: Path) -> int | None:
    try:
        return p.stat().st_size
    except OSError:
        return None


def check_store(name: str, live_path: Path) -> dict[str, Any]:
    """Read-only health report: tables, row counts, live + snapshot sizes."""
    import duckdb  # noqa: PLC0415

    from src.index._snapshot import snapshot_path_for  # noqa: PLC0415

    snap = snapshot_path_for(live_path)
    counts: dict[str, int | None] = {}
    error: str | None = None
    con = None
    try:
        con = duckdb.connect(str(live_path), read_only=True)
        tables = [
            t for (t,) in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'",
            ).fetchall()
        ]
        for t in tables:
            try:
                counts[t] = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            except Exception:  # noqa: BLE001
                counts[t] = None
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    finally:
        if con is not None:
            con.close()
    return {
        "store": name,
        "live_path": str(live_path),
        "live_bytes": _file_bytes(live_path),
        "snapshot_present": snap.exists(),
        "snapshot_bytes": _file_bytes(snap) if snap.exists() else None,
        "tables": counts,
        **({"error": error} if error else {}),
    }


def optimize_store(name: str, live_path: Path) -> dict[str, Any]:
    """Checkpoint the live file and (re)publish the compacted `.ro` snapshot."""
    import duckdb  # noqa: PLC0415

    from src.index._snapshot import publish_snapshot, snapshot_path_for  # noqa: PLC0415

    snap = snapshot_path_for(live_path)
    before = _file_bytes(snap) if snap.exists() else None
    result: dict[str, Any]
    con = None
    try:
        con = duckdb.connect(str(live_path))  # writer — needs exclusive access
        result = publish_snapshot(con, live_path, force=True)
    except Exception as exc:  # noqa: BLE001 — never abort the whole sweep on one store
        result = {"published": False, "error": str(exc)}
    finally:
        if con is not None:
            con.close()
    return {
        "store": name,
        "live_bytes": _file_bytes(live_path),
        "snapshot_bytes_before": before,
        "snapshot_bytes_after": _file_bytes(snap) if snap.exists() else None,
        "result": result,
    }


def run(*, only: str | None = None, check: bool = False) -> dict[str, Any]:
    root = index_data_root()
    stores = discover_stores(only)
    if not stores:
        return {
            "data_root": str(root),
            "stores": [],
            "note": f"no live DuckDB stores found under {root}",
        }
    fn = check_store if check else optimize_store
    return {
        "data_root": str(root),
        "mode": "check" if check else "optimize",
        "stores": [fn(name, path) for name, path in stores],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-store DuckDB maintenance.")
    parser.add_argument("--store", help="operate on this store only")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report rows/sizes/snapshot staleness; do not write",
    )
    args = parser.parse_args(argv)
    print(json.dumps(run(only=args.store, check=args.check), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
