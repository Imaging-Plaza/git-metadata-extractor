"""Local-CSV ingest pipeline.

Reads the SNSF P3 bulk CSV set the user manually downloaded and dropped
into a directory (default: `data/index/snsf/raw/`). Loads each CSV into
the matching DuckDB table via `read_csv_auto`, then derives the active
scope's membership rows from the `grants` table.

This is the canonical Phase 1 ingest path. The earlier API-based path is
parked (see `.internal/snsf/README.md` for why).
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Optional

from src.index.snsf.config import SnsfIndexConfig
from src.index.snsf.ingest.scope import where_for
from src.index.snsf.models import IngestManifest, IngestSummary
from src.index.snsf.storage.duckdb_store import SnsfStore

LOGGER = logging.getLogger(__name__)


# Default location where the user is expected to drop the manual CSV download.
# Configurable via `--source-dir` on the CLI or `SNSF_SOURCE_DIR` env override.
DEFAULT_SOURCE_DIR = Path("data/index/snsf/raw")

# Filenames the loader looks for. Missing files just skip that loader (the
# CSV set may be partial — e.g. user only wants grants without persons).
GRANTS_FILE = "grants_with_abstracts.csv"
PERSONS_FILE = "persons.csv"
DISCIPLINES_FILE = "SNF_field_of_research_disciplines.csv"

# (csv filename, store-method-name, log-label).
# Loader methods on `SnsfStore` all take a single Path arg and return an int.
OUTPUT_LOADERS: tuple[tuple[str, str, str], ...] = (
    ("output_data_scientific_publications.csv", "load_output_publications",          "publications"),
    ("output_data_academicevents.csv",          "load_output_academic_events",       "academic events"),
    ("output_data_collaborations.csv",          "load_output_collaborations",        "collaborations"),
    ("output_data_datasets.csv",                "load_output_datasets",              "datasets"),
    ("output_data_knowledgetransfer.csv",       "load_output_knowledge_transfers",   "knowledge transfers"),
    ("output_data_publiccommunications.csv",    "load_output_public_communications", "public communications"),
    ("output_data_useinspired.csv",             "load_output_use_inspired",          "use-inspired outputs"),
)


def resolve_source_dir(explicit: Optional[Path] = None) -> Path:
    """Resolve the source directory; explicit > env override > default."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    import os

    env = os.getenv("SNSF_SOURCE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_SOURCE_DIR.resolve()


def run(
    cfg: SnsfIndexConfig,
    *,
    source_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    skip_persons: bool = False,
    skip_disciplines: bool = False,
    skip_outputs: bool = False,
    scope_mode: Optional[str] = None,
) -> IngestSummary:
    """Run a full local-CSV ingest. Returns a printable summary."""
    src = resolve_source_dir(source_dir)
    if not src.exists():
        msg = (
            f"Source dir not found: {src}. Place the SNSF bulk CSVs there or "
            f"override with --source-dir / SNSF_SOURCE_DIR. Expected files: "
            f"{GRANTS_FILE}, {PERSONS_FILE}, {DISCIPLINES_FILE}."
        )
        raise FileNotFoundError(msg)

    LOGGER.info("Ingesting SNSF bulk CSVs from %s", src)
    store = SnsfStore.open(db_path)

    grants_csv = src / GRANTS_FILE
    grants_n = store.load_grants(grants_csv)
    LOGGER.info("Loaded %d grants from %s", grants_n, grants_csv.name)

    persons_n = 0
    if not skip_persons:
        persons_csv = src / PERSONS_FILE
        if persons_csv.exists():
            persons_n = store.load_persons(persons_csv)
            LOGGER.info("Loaded %d persons from %s", persons_n, persons_csv.name)
        else:
            LOGGER.warning("Skipping persons (file not found): %s", persons_csv)

    disc_n = 0
    if not skip_disciplines:
        disc_csv = src / DISCIPLINES_FILE
        if disc_csv.exists():
            disc_n = store.load_disciplines(disc_csv)
            LOGGER.info("Loaded %d discipline mappings from %s", disc_n, disc_csv.name)
        else:
            LOGGER.warning("Skipping disciplines (file not found): %s", disc_csv)

    output_counts: dict[str, int] = {}
    if not skip_outputs:
        for filename, method_name, label in OUTPUT_LOADERS:
            csv = src / filename
            if not csv.exists():
                LOGGER.warning("Skipping %s (file not found): %s", label, csv)
                continue
            loader = getattr(store, method_name)
            n = loader(csv)
            output_counts[label] = n
            LOGGER.info("Loaded %d %s from %s", n, label, csv.name)

    active_scope = scope_mode or cfg.scope.active
    where_sql, where_params = where_for(active_scope)
    scope_n = store.replace_scope_records_by_filter(
        active_scope, where_sql, where_params,
    )
    LOGGER.info(
        "Derived scope_records for %r → %d grants (filter: %s)",
        active_scope, scope_n, where_sql,
    )

    snapshot = _detect_snapshot_iso(grants_csv)
    store.set_manifest(
        IngestManifest(
            scope_mode=active_scope,
            record_count=scope_n,
            snapshot_iso=snapshot,
            source_dir=str(src),
        ),
    )

    summary = IngestSummary(
        source_dir=str(src),
        grants_loaded=grants_n,
        persons_loaded=persons_n,
        disciplines_loaded=disc_n,
        outputs_loaded=output_counts,
        scope_mode=active_scope,
        scope_grants=scope_n,
        snapshot_iso=snapshot,
    )
    store.close()
    return summary


def _detect_snapshot_iso(grants_csv: Path) -> str:
    """Approximate the dump's snapshot date from the CSV's mtime."""
    if not grants_csv.exists():
        return dt.datetime.now(dt.timezone.utc).isoformat()
    mtime = dt.datetime.fromtimestamp(grants_csv.stat().st_mtime, tz=dt.timezone.utc)
    return mtime.isoformat()


__all__ = ["DEFAULT_SOURCE_DIR", "resolve_source_dir", "run"]
