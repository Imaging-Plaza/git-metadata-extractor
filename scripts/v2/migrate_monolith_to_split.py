"""Migrate the 3.0.0rc1 monolithic index DuckDBs into the new split stores.

The 3.0.0rc1 restructuring split the old monolithic per-source DuckDBs
(``github.duckdb``, ``huggingface.duckdb``, ``zenodo.duckdb``) into one
store per entity type (``github_repos``, ``huggingface_models``, …). The
*data migration* into that new layout was never run: the split stores
ship with only a handful of scaffolding rows, while the full corpus (~7k
GitHub repos, ~1k HF models, ~24k Zenodo files, …) still lives ONLY in
the orphaned monolithic files on disk.

This script moves that data across **without re-fetching from the
network**. The old and new table schemas are identical — with a single
exception (the HuggingFace ``orgs`` table dropped three columns,
``namespace_kind`` / ``scope`` / ``source``, so it is copied on the
common column subset) — so for each table it ATTACHes the orphan
read-only and runs an idempotent ``INSERT … ON CONFLICT DO NOTHING`` into
the split store (every target table has a primary key, so re-running is a
no-op). After the copy, pass ``--embed`` to rebuild each store's Qdrant
collection from the now-populated DuckDB (DuckDB → RCP → Qdrant); that
step needs the RCP inference host reachable.

Entities with no monolithic source (``github_organizations``,
``github_users``, ``huggingface_users``, ``huggingface_papers``,
``dockerhub``) are NOT handled here — they never existed in the old
layout and must be (re)populated via their ``POST /v2/indices/<p>/ingest``
routes.

SAFETY
------
- The orphan monolithic files are opened **READ-ONLY** and never
  modified; they remain the canonical backup. Nothing is ever deleted.
- Default mode is a dry run: it only reports per-table source/target row
  counts. Pass ``--apply`` to actually write.
- The script opens the split DuckDBs read-WRITE. DuckDB is single-writer,
  so **stop the GME server first** — otherwise the open fails with
  ``Could not set lock on file`` (handled here with a clear message).

Usage
-----
    python scripts/v2/migrate_monolith_to_split.py                  # dry run, all
    python scripts/v2/migrate_monolith_to_split.py --apply          # copy rows
    python scripts/v2/migrate_monolith_to_split.py --apply --embed  # + rebuild Qdrant
    python scripts/v2/migrate_monolith_to_split.py --provider github_repos --apply
"""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

# Allow running as a plain script (`python scripts/v2/migrate_monolith_to_split.py`).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@dataclass(frozen=True)
class ProviderPlan:
    """One provider's migration: where the old data is, where it goes, and
    how to rebuild its vectors."""

    provider: str
    old_db: str  # path to the orphan monolithic DuckDB (read-only source)
    store_module: str
    store_class: str
    embed_module: str
    embed_callable: str
    # (old_table_name, new_table_name) pairs, copied in listed order so
    # parent rows land before the junction tables that FK-reference them.
    tables: tuple[tuple[str, str], ...]


# Order within `tables` matters for zenodo: records/creators/communities
# before the record_* junction tables.
PLAN: tuple[ProviderPlan, ...] = (
    ProviderPlan(
        provider="github_repos",
        old_db="data/index/github/duckdb/github.duckdb",
        store_module="src.index.github_repos.storage.duckdb_store",
        store_class="GitHubReposStore",
        embed_module="src.index.github_repos.embed.pipeline",
        embed_callable="embed_repos",
        tables=(("repos", "repos"),),
    ),
    ProviderPlan(
        provider="huggingface_models",
        old_db="data/index/huggingface/duckdb/huggingface.duckdb",
        store_module="src.index.huggingface_models.storage.duckdb_store",
        store_class="HuggingFaceModelsStore",
        embed_module="src.index.huggingface_models.embed.pipeline",
        embed_callable="embed_models",
        tables=(("models", "models"),),
    ),
    ProviderPlan(
        provider="huggingface_datasets",
        old_db="data/index/huggingface/duckdb/huggingface.duckdb",
        store_module="src.index.huggingface_datasets.storage.duckdb_store",
        store_class="HuggingFaceDatasetsStore",
        embed_module="src.index.huggingface_datasets.embed.pipeline",
        embed_callable="embed_datasets",
        tables=(("datasets", "datasets"),),
    ),
    ProviderPlan(
        provider="huggingface_organizations",
        old_db="data/index/huggingface/duckdb/huggingface.duckdb",
        store_module="src.index.huggingface_organizations.storage.duckdb_store",
        store_class="HuggingFaceOrganizationsStore",
        embed_module="src.index.huggingface_organizations.embed.pipeline",
        embed_callable="embed_organizations",
        # old `orgs` has 3 extra columns; copied on the common subset.
        tables=(("orgs", "organizations"),),
    ),
    ProviderPlan(
        provider="huggingface_spaces",
        old_db="data/index/huggingface/duckdb/huggingface.duckdb",
        store_module="src.index.huggingface_spaces.storage.duckdb_store",
        store_class="HuggingFaceSpacesStore",
        embed_module="src.index.huggingface_spaces.embed.pipeline",
        embed_callable="embed_spaces",
        tables=(("spaces", "spaces"),),
    ),
    ProviderPlan(
        provider="zenodo_records",
        old_db="data/index/zenodo/duckdb/zenodo.duckdb",
        store_module="src.index.zenodo_records.storage.duckdb_store",
        store_class="ZenodoRecordsStore",
        embed_module="src.index.zenodo_records.embed.pipeline",
        embed_callable="embed_records",
        tables=(
            ("records", "records"),
            ("creators", "creators"),
            ("communities", "communities"),
            ("record_creators", "record_creators"),
            ("record_communities", "record_communities"),
        ),
    ),
)


def _new_db_path(provider: str) -> Path:
    """Resolve the split store's DuckDB path from its own config loader."""
    cfg = importlib.import_module(f"src.index.{provider}.config").load_config()
    return Path(cfg.paths.duckdb_path)


def _columns(conn: duckdb.DuckDBPyConnection, table_ref: str) -> list[str]:
    """Columns of a table. ``table_ref`` is an already-formed SQL reference
    (e.g. ``"repos"`` or ``old."repos"``), not quoted further here."""
    return [d[0] for d in conn.execute(f"SELECT * FROM {table_ref} LIMIT 0").description]


def _count(conn: duckdb.DuckDBPyConnection, table_ref: str) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {table_ref}").fetchone()[0])


@dataclass
class TableResult:
    old_table: str
    new_table: str
    source_rows: int
    target_before: int
    target_after: int
    dropped_columns: list[str]

    @property
    def inserted(self) -> int:
        return self.target_after - self.target_before


def _copy_table(
    new_conn: duckdb.DuckDBPyConnection,
    old_table: str,
    new_table: str,
    *,
    apply: bool,
) -> TableResult:
    """Copy one table from the ATTACHed orphan (`old`) into the split store.

    Idempotent via the target's primary key (``ON CONFLICT DO NOTHING``).
    Only the columns present in BOTH schemas are copied; any column that
    exists solely in the old table is reported as dropped.
    """
    old_ref = f'old."{old_table}"'
    new_ref = f'"{new_table}"'
    old_cols = set(_columns(new_conn, old_ref))
    new_cols = _columns(new_conn, new_ref)  # target order
    common = [c for c in new_cols if c in old_cols]
    dropped = sorted(old_cols - set(new_cols))

    source_rows = _count(new_conn, old_ref)
    before = _count(new_conn, new_ref)

    if apply:
        col_list = ", ".join(f'"{c}"' for c in common)
        new_conn.execute(
            f"INSERT INTO {new_ref} ({col_list}) "
            f"SELECT {col_list} FROM {old_ref} "
            f"ON CONFLICT DO NOTHING",
        )

    after = _count(new_conn, new_ref)
    return TableResult(old_table, new_table, source_rows, before, after, dropped)


def _migrate_provider(plan: ProviderPlan, *, apply: bool) -> list[TableResult] | None:
    old_path = _PROJECT_ROOT / plan.old_db
    if not old_path.exists():
        print(f"  ! orphan source missing, skipping: {plan.old_db}")
        return None
    new_path = _new_db_path(plan.provider)
    if not new_path.exists():
        print(f"  ! split store missing, skipping: {new_path}")
        return None

    try:
        conn = duckdb.connect(str(new_path))  # read-WRITE target
    except duckdb.IOException as exc:
        if "lock" in str(exc).lower():
            print(
                f"  ✗ cannot lock {new_path.name} — STOP the GME server first "
                f"(DuckDB is single-writer).",
            )
            return None
        raise

    try:
        conn.execute(f"ATTACH '{old_path}' AS old (READ_ONLY)")
        results = [
            _copy_table(conn, ot, nt, apply=apply) for ot, nt in plan.tables
        ]
    finally:
        conn.close()
    return results


def _run_embed(plan: ProviderPlan) -> str:
    """Rebuild the provider's Qdrant collection from its (now populated)
    DuckDB by invoking the same embed pipeline the ingest job uses."""
    cfg = importlib.import_module(f"src.index.{plan.provider}.config").load_config()
    store_cls = getattr(
        importlib.import_module(plan.store_module), plan.store_class,
    )
    embed_fn = getattr(
        importlib.import_module(plan.embed_module), plan.embed_callable,
    )
    store = store_cls.open(cfg.paths.duckdb_path)
    try:
        summary: Any = embed_fn(config=cfg, store=store)
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    return str(summary)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Copy data from the orphan monolithic DuckDBs into the 3.0.0rc1 "
            "split stores (read-only on the source; idempotent on the target)."
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write. Without it, only report source/target counts.",
    )
    parser.add_argument(
        "--embed", action="store_true",
        help="After copying, rebuild each provider's Qdrant collection "
             "(DuckDB → RCP → Qdrant). Needs RCP reachable. Implies work over "
             "the network.",
    )
    parser.add_argument(
        "--provider", action="append", choices=[p.provider for p in PLAN],
        help="Limit to one or more providers (repeatable). Default: all.",
    )
    args = parser.parse_args()

    selected = [p for p in PLAN if not args.provider or p.provider in args.provider]
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"== monolith → split migration ({mode}) ==")
    if not args.apply:
        print("  (no writes — pass --apply to copy; --embed to rebuild Qdrant)")

    grand_inserted = 0
    embed_targets: list[ProviderPlan] = []
    for plan in selected:
        print(f"\n[{plan.provider}]  ⟵  {plan.old_db}")
        results = _migrate_provider(plan, apply=args.apply)
        if not results:
            continue
        provider_inserted = 0
        for r in results:
            verb = "inserted" if args.apply else "source"
            n = r.inserted if args.apply else r.source_rows
            extra = f"  (target {r.target_before}→{r.target_after})" if args.apply else \
                    f"  (target has {r.target_before})"
            drop = f"  [dropped cols: {', '.join(r.dropped_columns)}]" if r.dropped_columns else ""
            print(f"    {r.old_table:18s} -> {r.new_table:18s} {verb}={n:>6}{extra}{drop}")
            provider_inserted += r.inserted
        grand_inserted += provider_inserted
        if args.apply and provider_inserted > 0:
            embed_targets.append(plan)

    if args.apply:
        print(f"\nTotal rows inserted: {grand_inserted}")

    if args.embed and args.apply:
        print("\n== embed (rebuild Qdrant from DuckDB) ==")
        for plan in embed_targets:
            print(f"[{plan.provider}] embedding…")
            try:
                print(f"    {_run_embed(plan)}")
            except Exception as exc:  # noqa: BLE001 — report, keep going
                print(f"    ✗ embed failed: {type(exc).__name__}: {exc}")
    elif args.embed and not args.apply:
        print("\n(--embed ignored in dry-run; combine with --apply)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
