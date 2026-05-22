"""Tiny CLI: `python -m src.index.communities.cli build`."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.index.communities.build import DEFAULT_CONFIG, build_from_config
from src.index.communities.paths import duckdb_path
from src.index.communities.storage.duckdb_store import CommunitiesStore


def _cmd_build(args: argparse.Namespace) -> None:
    summary = build_from_config(
        config_path=Path(args.config),
        include_discovery=not args.no_discovery,
    )
    total = sum(summary.values())
    print(json.dumps({
        "ingested_by_parent": summary,
        "total": total,
        "duckdb_path": str(duckdb_path()),
    }, indent=2))


def _cmd_stats(_args: argparse.Namespace) -> None:
    store = CommunitiesStore.open()
    with store.read_only() as con:
        total = con.execute("SELECT COUNT(*) FROM communities").fetchone()[0]
        by_parent = con.execute(
            "SELECT parent_org, COUNT(*) FROM communities GROUP BY parent_org ORDER BY 2 DESC",
        ).fetchall()
    print(json.dumps({
        "total": total,
        "by_parent": dict(by_parent),
        "duckdb_path": str(duckdb_path()),
    }, indent=2))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="communities-index")
    sub = parser.add_subparsers(dest="cmd", required=True)

    build_p = sub.add_parser("build", help="Ingest communities from the config file.")
    build_p.add_argument("--config", default=str(DEFAULT_CONFIG))
    build_p.add_argument(
        "--no-discovery",
        action="store_true",
        help="Skip the `?q=<keyword>` auto-discovery; ingest only hardcoded slugs.",
    )
    build_p.set_defaults(func=_cmd_build)

    stats_p = sub.add_parser("stats", help="Show row counts for the current DuckDB.")
    stats_p.set_defaults(func=_cmd_stats)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
