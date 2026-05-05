"""CLI for the SNSF P3 index (Phase 1).

Usage:
    python -m src.index.snsf load-local [--source-dir PATH] [--scope SCOPE]
                                        [--db-path PATH] [--skip-persons]
                                        [--skip-disciplines]
    python -m src.index.snsf stats [--scope SCOPE]
    python -m src.index.snsf lookup --grant-number 123456
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.index.snsf.config import DEFAULT_CONFIG_PATH, load_config
from src.index.snsf.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.index.snsf")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"Path to YAML config (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_load = sub.add_parser(
        "load-local",
        help=(
            "Ingest the SNSF bulk CSVs from a local directory into DuckDB. "
            "User must download the CSVs first — see .internal/snsf/README.md."
        ),
    )
    p_load.add_argument(
        "--source-dir", type=Path, default=None,
        help="Directory holding the CSV set (default: data/index/snsf/raw/)",
    )
    p_load.add_argument(
        "--db-path", type=Path, default=None,
        help="Override DuckDB file path (default: <data>/snsf/duckdb/snsf.duckdb)",
    )
    p_load.add_argument(
        "--scope", default=None,
        help="Override active scope mode (epfl|ethz|eth_domain|switzerland)",
    )
    p_load.add_argument(
        "--skip-persons", action="store_true",
        help="Don't load persons.csv",
    )
    p_load.add_argument(
        "--skip-disciplines", action="store_true",
        help="Don't load SNF_field_of_research_disciplines.csv",
    )
    p_load.add_argument(
        "--skip-outputs", action="store_true",
        help="Don't load output_data_*.csv (publications/datasets/etc.)",
    )

    p_stats = sub.add_parser("stats", help="Show manifest + scope counts from DuckDB")
    p_stats.add_argument(
        "--scope", default=None,
        help="Scope mode to report (default: cfg.scope.active)",
    )

    p_lookup = sub.add_parser("lookup", help="Fetch a single grant row by GrantNumber")
    p_lookup.add_argument("--grant-number", type=int, required=True)

    p_embed = sub.add_parser(
        "embed",
        help="Embed all scope grants via RCP + upsert to Qdrant snsf_<scope>",
    )
    p_embed.add_argument(
        "--scope", default=None,
        help="Scope to embed (default: cfg.scope.active)",
    )
    p_embed.add_argument(
        "--keep-existing", action="store_true",
        help="Keep the existing Qdrant collection (default: drop+recreate)",
    )

    p_query = sub.add_parser("query", help="Semantic search via Qdrant + RCP rerank")
    p_query.add_argument("text")
    p_query.add_argument("--top-k", type=int, default=None)
    p_query.add_argument("--rerank-top-k", type=int, default=None)
    p_query.add_argument("--scope", default=None)
    p_query.add_argument("--institution", default=None,
                         help="Filter by research_institution payload field (exact match)")
    p_query.add_argument("--institute", default=None,
                         help=(
                             "Post-filter by `institute` (specific lab/centre name) "
                             "via DuckDB lookup. Substring match, case-insensitive. "
                             "Example: --institute 'Swiss Data Science Center'."
                         ))
    p_query.add_argument("--discipline-l1", default=None,
                         help="Filter by main_discipline_l1 payload field")
    p_query.add_argument("--state", default=None,
                         help="Filter by state payload field (e.g. 'completed', 'ongoing')")

    p_link = sub.add_parser(
        "orcid-link",
        help=(
            "Look up an ORCID in both SNSF persons and OpenAlex authors. "
            "Returns the person's SNSF grants + OpenAlex works."
        ),
    )
    p_link.add_argument("--orcid", required=True, help="ORCID (any format)")
    p_link.add_argument("--snsf-scope", default=None,
                        help="Limit SNSF grants to this scope (epfl|ethz|...|switzerland)")
    p_link.add_argument("--grant-limit", type=int, default=50)
    p_link.add_argument("--work-limit",  type=int, default=50)

    p_cov = sub.add_parser(
        "orcid-coverage",
        help="Report how many SNSF persons (in scope) link to OpenAlex authors via ORCID.",
    )
    p_cov.add_argument("--snsf-scope", default=None,
                       help="SNSF scope to count over (default: all)")

    return parser


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    if args.cmd == "load-local":
        from src.index.snsf.ingest import local_ingest
        cfg = load_config(args.config)
        summary = local_ingest.run(
            cfg,
            source_dir=args.source_dir,
            db_path=args.db_path,
            skip_persons=args.skip_persons,
            skip_disciplines=args.skip_disciplines,
            skip_outputs=args.skip_outputs,
            scope_mode=args.scope,
        )
        _print_json(summary.model_dump())
        return 0

    if args.cmd == "stats":
        cfg = load_config(args.config)
        scope = args.scope or cfg.scope.active
        store = DuckDBStore.open()
        try:
            counts = {
                "grants_total":  store.count_grants(),
                "persons_total": store.count_persons(),
                "disciplines_total": store.count_disciplines(),
                "scope": scope,
                "scope_grants": store.count_scope_records(scope),
                "manifest": store.fetch_manifest(scope),
            }
        finally:
            store.close()
        if counts["manifest"] is None:
            print(
                f"No manifest in DuckDB for scope {scope!r}. "
                f"Run `python -m src.index.snsf load-local` first.",
                file=sys.stderr,
            )
        _print_json(counts)
        return 0 if counts["manifest"] is not None else 1

    if args.cmd == "lookup":
        store = DuckDBStore.open()
        try:
            row = store.fetch_grant(args.grant_number)
        finally:
            store.close()
        if row is None:
            print(f"GrantNumber {args.grant_number} not found", file=sys.stderr)
            return 1
        _print_json(row)
        return 0

    if args.cmd == "embed":
        from src.index.snsf import embed_pipeline
        cfg = load_config(args.config)
        summary = embed_pipeline.run_sync(
            cfg,
            scope_mode=args.scope,
            recreate=not args.keep_existing,
        )
        _print_json(summary)
        return 0

    if args.cmd == "orcid-link":
        from src.index.snsf.orcid_link import link_by_orcid
        result = link_by_orcid(
            args.orcid,
            snsf_scope=args.snsf_scope,
            grant_limit=args.grant_limit,
            work_limit=args.work_limit,
        )
        _print_json(result)
        return 0

    if args.cmd == "orcid-coverage":
        from src.index.snsf.orcid_link import coverage_report
        _print_json(coverage_report(args.snsf_scope))
        return 0

    if args.cmd == "query":
        from src.index.snsf.query import query_rag_sync
        cfg = load_config(args.config)
        results = query_rag_sync(
            cfg, args.text,
            top_k=args.top_k,
            rerank_top_k=args.rerank_top_k,
            institution=args.institution,
            institute=args.institute,
            discipline_l1=args.discipline_l1,
            state=args.state,
            scope_mode=args.scope,
        )
        _print_json(results)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
