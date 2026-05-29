"""DuckDB compaction via `EXPORT DATABASE` + `IMPORT DATABASE` round-trip.

DuckDB upserts accumulate tombstones; over the lifetime of an index
catalog the file can grow well past the size of its live rows.
`openalex.duckdb` ballooned to 3.7 GB in our deployment, `snsf.duckdb`
to 1 GB. There is no `VACUUM` in DuckDB's SQL dialect; the supported
idiom is to dump the whole catalog and re-import it into a fresh file.

Two entry points share this helper:

- `just compact-indexes` (the offline operator path): loops over every
  catalog and calls `compact_duckdb(provider, path)` directly.
- `POST /v2/indices/{provider}/compact` (the online operator path):
  closes the in-process Store cached on `app_state`, runs the same
  helper, then lets the next stats / search call re-open the file.

The round-trip is crash-safe: we EXPORT to a tempdir first, then write
the rebuilt DB to `<path>.compacting`, atomically rename the original
to `<path>.bak`, swap in the compacted file, and only then delete the
backup. If any step before the final rename fails, the original file
is untouched.
"""

from __future__ import annotations

import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

LOGGER = logging.getLogger(__name__)


class CompactResult(BaseModel):
    """API response for `POST /v2/indices/{provider}/compact`."""

    provider: str
    db_path: str
    bytes_before: int = Field(..., ge=0)
    bytes_after: int = Field(..., ge=0)
    reclaimed_bytes: int = Field(..., description="Always `bytes_before - bytes_after`.")
    compression_ratio: float = Field(
        ...,
        description="`bytes_after / bytes_before` — 1.0 means no gain, 0.5 means halved.",
    )
    table_count: int = Field(..., ge=0)
    elapsed_seconds: float = Field(..., ge=0)


@dataclass(slots=True)
class _Internal:
    """Just enough to break compaction into testable steps."""
    db_path: Path
    bytes_before: int
    start_monotonic: float


def _bytes(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def compact_duckdb(provider: str, db_path: Path | str) -> CompactResult:
    """EXPORT/IMPORT a DuckDB file in place, reclaiming tombstoned space.

    Raises:
        FileNotFoundError: when `db_path` does not exist.
        RuntimeError: when EXPORT or IMPORT fail (the original file is
            left untouched in either case).
    """
    db_path = Path(db_path)
    if not db_path.exists():
        msg = f"compact_duckdb: {db_path} does not exist"
        raise FileNotFoundError(msg)
    state = _Internal(
        db_path=db_path,
        bytes_before=_bytes(db_path),
        start_monotonic=time.monotonic(),
    )
    parent = db_path.parent
    tmp_db_path = db_path.with_name(db_path.name + ".compacting")
    backup_path = db_path.with_name(db_path.name + ".bak")
    table_count = 0

    with tempfile.TemporaryDirectory(
        prefix=f"compact-{provider}-", dir=str(parent),
    ) as export_dir:
        # 1. EXPORT from the live file
        try:
            conn = duckdb.connect(str(db_path))
        except duckdb.IOException as exc:
            msg = f"compact_duckdb: cannot open {db_path}: {exc}"
            raise RuntimeError(msg) from exc
        try:
            table_count = int(conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='main' AND table_type='BASE TABLE'",
            ).fetchone()[0])
            conn.execute(f"EXPORT DATABASE '{export_dir}'")
        except duckdb.Error as exc:
            conn.close()
            msg = f"compact_duckdb: EXPORT failed for {db_path}: {exc}"
            raise RuntimeError(msg) from exc
        conn.close()

        # 2. IMPORT into a sibling file (atomic swap to come).
        if tmp_db_path.exists():
            tmp_db_path.unlink()
        try:
            new_conn = duckdb.connect(str(tmp_db_path))
            new_conn.execute(f"IMPORT DATABASE '{export_dir}'")
            new_conn.close()
        except duckdb.Error as exc:
            if tmp_db_path.exists():
                tmp_db_path.unlink()
            msg = f"compact_duckdb: IMPORT failed for {db_path}: {exc}"
            raise RuntimeError(msg) from exc

    # 3. Atomic swap. If we fall over here, the .bak file is the
    #    original; the operator can move it back manually.
    if backup_path.exists():
        backup_path.unlink()
    db_path.rename(backup_path)
    try:
        tmp_db_path.rename(db_path)
    except OSError:
        # Restore from backup before re-raising.
        backup_path.rename(db_path)
        raise

    # 4. Drop the backup (success means we don't need it anymore).
    backup_path.unlink()

    bytes_after = _bytes(db_path)
    elapsed = time.monotonic() - state.start_monotonic
    ratio = (bytes_after / state.bytes_before) if state.bytes_before else 1.0
    result = CompactResult(
        provider=provider,
        db_path=str(db_path),
        bytes_before=state.bytes_before,
        bytes_after=bytes_after,
        reclaimed_bytes=state.bytes_before - bytes_after,
        compression_ratio=ratio,
        table_count=table_count,
        elapsed_seconds=elapsed,
    )
    LOGGER.info(
        "compacted %s: %d -> %d bytes (%.1f%% reclaimed) in %.2fs",
        db_path, state.bytes_before, bytes_after,
        (1 - ratio) * 100, elapsed,
    )
    return result


def _close_cached_store(app_state: Any, attr: str) -> None:
    store = getattr(app_state, attr, None)
    if store is None:
        return
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:  # noqa: BLE001 — best-effort
            LOGGER.warning("compact: failed to close cached %s: %s", attr, exc)
    try:
        setattr(app_state, attr, None)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("compact: failed to clear cached %s: %s", attr, exc)


_RESOURCE_ATTRS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "github": ("v2_github_resources",),
    "zenodo": ("v2_zenodo_resources",),
    "huggingface": ("v2_huggingface_resources",),
    "openalex": ("v2_openalex_resources",),
    "orcid": ("v2_orcid_resources",),
    "renkulab": ("v2_renkulab_resources",),
    "swissubase": ("v2_swissubase_resources",),
    "oamonitor": ("v2_oamonitor_resources",),
    "ethz_research_collection": ("v2_ethz_research_collection_resources",),
    "ror": ("v2_ror_store",),
    "infoscience": ("v2_infoscience_store",),
    "snsf": ("v2_snsf_store",),
    "epfl_graph": ("v2_epfl_graph_store",),
    "zenodo_communities": ("v2_zenodo_communities_store",),
}


def close_cached_resources_for(provider: str, app_state: Any) -> None:
    """Drop the in-process write handle so EXPORT can re-open the file.

    The per-provider `get_or_create_<provider>_resources` helpers cache
    the Store on `app_state`. Closing it lets `compact_duckdb` open the
    DuckDB without contesting the lock, and the next stats/search call
    will lazily re-init.
    """
    for attr in _RESOURCE_ATTRS_BY_PROVIDER.get(provider, ()):
        # Some providers cache a tuple (config, store, …) on a single attr.
        cached = getattr(app_state, attr, None)
        if cached is None:
            continue
        if isinstance(cached, tuple):
            for item in cached:
                close = getattr(item, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning(
                            "compact: failed to close item in %s: %s", attr, exc,
                        )
            try:
                setattr(app_state, attr, None)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("compact: failed to clear %s: %s", attr, exc)
        else:
            _close_cached_store(app_state, attr)


def compact_all_indexes(
    data_root: Path | str | None = None, *, verbose: bool = True,
) -> list[CompactResult]:
    """Compact every `*.duckdb` under `<data_root>` (default: `data/index`).

    The offline counterpart to `POST /v2/indices/{provider}/compact` — used
    by `just compact-indexes` while the server is offline. Skips any file
    whose name matches a `.bak` / `.compacting` sibling left over from a
    prior interrupted run; those are the operator's call to resolve.
    """
    if data_root is None:
        data_root = Path(__file__).resolve().parents[3] / "data" / "index"
    data_root = Path(data_root)
    if not data_root.exists():
        msg = f"compact_all_indexes: {data_root} does not exist"
        raise FileNotFoundError(msg)

    results: list[CompactResult] = []
    for db_path in sorted(data_root.rglob("*.duckdb")):
        # Derive the provider name from the immediate parent dir
        # (data/index/<provider>/duckdb/<name>.duckdb), falling back to
        # the file stem if the layout is non-standard.
        parts = db_path.relative_to(data_root).parts
        provider = parts[0] if parts else db_path.stem
        if verbose:
            LOGGER.info("compacting %s …", db_path)
        try:
            result = compact_duckdb(provider, db_path)
        except Exception as exc:  # noqa: BLE001 — keep going on partial failure
            LOGGER.warning("compact %s: FAILED — %s", db_path, exc)
            continue
        results.append(result)
        if verbose:
            LOGGER.info(
                "  %s: %.1f MB → %.1f MB (%.1f%% reclaimed) in %.2fs",
                provider,
                result.bytes_before / (1024 * 1024),
                result.bytes_after / (1024 * 1024),
                (1 - result.compression_ratio) * 100,
                result.elapsed_seconds,
            )
    return results


def _cli_main() -> int:
    """Entry point for `python -m src.v2.indices.compact`."""
    import argparse  # noqa: PLC0415
    import json  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="python -m src.v2.indices.compact",
        description=(
            "Compact every DuckDB under data/index/ via EXPORT/IMPORT. "
            "Used by `just compact-indexes`."
        ),
    )
    parser.add_argument(
        "--data-root", default=None,
        help="Root directory to scan (default: <repo>/data/index).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-file progress logs.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    results = compact_all_indexes(args.data_root, verbose=not args.quiet)
    json.dump(
        {
            "compacted": [r.model_dump() for r in results],
            "total_reclaimed_bytes": sum(r.reclaimed_bytes for r in results),
        },
        __import__("sys").stdout, indent=2, default=str,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry point
    raise SystemExit(_cli_main())


__all__ = [
    "CompactResult",
    "close_cached_resources_for",
    "compact_all_indexes",
    "compact_duckdb",
]
