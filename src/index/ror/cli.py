"""CLI for the ROR index.

Usage:
    python -m src.index.ror build [--config PATH] [--refresh]
    python -m src.index.ror query "<text>" [--top-k N] [--rerank-top-k N]
    python -m src.index.ror lookup [--name TEXT] [--ror-id ID] [--country CC] [--limit N]
    python -m src.index.ror stats
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .query import lookup_dump, query_rag_sync
from .store import read_manifest

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.index.ror")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"Path to YAML config (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Download dump, filter, embed, write index")
    p_build.add_argument("--refresh", action="store_true",
                         help="Force re-download of the Zenodo dump")

    p_query = sub.add_parser("query", help="Semantic RAG query over the embedded subset")
    p_query.add_argument("text")
    p_query.add_argument("--top-k", type=int, default=None)
    p_query.add_argument("--rerank-top-k", type=int, default=None)

    p_lookup = sub.add_parser("lookup", help="Lexical lookup over the FULL ROR dump")
    p_lookup.add_argument("--name", default=None, help="Name / token query")
    p_lookup.add_argument("--ror-id", default=None, help="Exact ROR ID (URL or bare)")
    p_lookup.add_argument("--country", default=None, help="ISO-3166 alpha-2 (e.g. CH)")
    p_lookup.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="Show manifest for the configured scope mode")

    p_migrate = sub.add_parser(
        "migrate",
        help="One-shot: copy existing FAISS vectors → Qdrant (no re-embedding)",
    )
    p_migrate.add_argument(
        "--scope", default="all",
        help="Scope to migrate (epfl_ethz|switzerland|europe|worldwide|all)",
    )

    p_ms = sub.add_parser(
        "migrate-storage",
        help=(
            "One-shot: port legacy JSONL+manifest sidecars + cached dump JSON "
            "into the DuckDB store (D16). Does not touch Qdrant beyond a "
            "read-only count check."
        ),
    )
    p_ms.add_argument(
        "--dump-path", type=Path, default=None,
        help="Override the auto-detected ROR dump JSON path",
    )
    p_ms.add_argument(
        "--db-path", type=Path, default=None,
        help="Override the DuckDB file path (default <data>/ror/duckdb/ror.duckdb)",
    )
    p_ms.add_argument(
        "--skip-qdrant-check", action="store_true",
        help="Don't compare DuckDB scope counts against Qdrant collections",
    )

    return parser


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    if args.cmd == "build":
        from . import build as build_mod
        cfg = load_config(args.config)
        summary = build_mod.run(cfg, refresh=args.refresh)
        _print_json(summary)
        return 0

    if args.cmd == "query":
        cfg = load_config(args.config)
        results = query_rag_sync(
            cfg, args.text,
            top_k=args.top_k, rerank_top_k=args.rerank_top_k,
        )
        _print_json([r.model_dump() for r in results])
        return 0

    if args.cmd == "lookup":
        cfg = load_config(args.config)
        results = lookup_dump(
            cfg,
            text=args.name,
            ror_id=args.ror_id,
            country=args.country,
            limit=args.limit,
        )
        _print_json([
            {"ror_id": r.ror_id, "name": r.name, "matched_tokens": r.matched_tokens}
            for r in results
        ])
        return 0

    if args.cmd == "stats":
        cfg = load_config(args.config)
        try:
            manifest = read_manifest(cfg.scope.mode)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        _print_json(manifest.model_dump())
        return 0

    if args.cmd == "migrate":
        from . import migrate as migrate_mod
        cfg = load_config(args.config)
        if args.scope == "all":
            results = migrate_mod.migrate_all(cfg)
        else:
            results = [migrate_mod.migrate_scope(cfg, args.scope)]
        _print_json(results)
        return 0

    if args.cmd == "migrate-storage":
        from .storage import migrate_storage
        cfg = load_config(args.config)
        summary = migrate_storage.migrate_all(
            cfg,
            db_path=args.db_path,
            dump_path=args.dump_path,
            skip_qdrant_check=args.skip_qdrant_check,
        )
        _print_json(summary)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
