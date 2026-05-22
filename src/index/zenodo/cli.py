"""CLI for the Zenodo index module.

Subcommands:

- `discover`   — find candidate Zenodo IDs from external sources (e.g. Infoscience).
- `ingest`     — pull Zenodo records into DuckDB (community- or id-filtered).
- `embed`      — chunk + embed records, push vectors to Qdrant.
- `search`     — semantic retrieval (vector + RCP rerank).
- `query`      — read-only SQL over DuckDB (predefined or guarded ad-hoc).
- `status`     — print row counts + Qdrant collection size + paths.
- `serve`      — run the FastAPI app.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from src.index.zenodo.config import load_config
from src.index.zenodo.embed.pipeline import ZENODO_COLLECTION, embed_records
from src.index.zenodo.ingest.discover import discover_from_infoscience
from src.index.zenodo.ingest.records import ingest_by_ids, ingest_records, load_ids_file
from src.index.zenodo.ingest.scope import resolve_scope
from src.index.zenodo.retrieval.semantic import semantic_search
from src.index.zenodo.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.zenodo.storage.duckdb_store import ZenodoStore

LOGGER = logging.getLogger(__name__)


def _emit_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config()
    if args.ids:
        from pathlib import Path

        ids = load_ids_file(Path(args.ids))
        if not ids:
            message = f"no parseable Zenodo IDs found in {args.ids}"
            raise SystemExit(message)
        store = ZenodoStore.open()
        try:
            summary = ingest_by_ids(
                config=config,
                store=store,
                ids=ids,
                refresh=args.refresh,
            )
        finally:
            store.close()
        _emit_json(
            {
                "mode": "ids",
                "source_file": args.ids,
                "requested": summary["requested"],
                "skipped_existing_count": len(summary["skipped_existing"]),
                "fetched": summary["fetched"],
                "persisted_count": len(summary["persisted"]),
                "missing_count": len(summary["missing"]),
                "failed_count": len(summary["failed"]),
                "missing": summary["missing"],
                "failed": summary["failed"],
            },
        )
        return 0

    scope = resolve_scope(args.scope, config)
    if not scope.communities:
        message = (
            f"scope={scope.name} has no communities configured. "
            "Edit config/index/zenodo.yaml under `scope.{name}_communities`."
        )
        raise SystemExit(message)
    store = ZenodoStore.open()
    try:
        summary = ingest_records(
            config=config,
            store=store,
            scope=scope,
            limit=args.limit,
            refresh=args.refresh,
        )
    finally:
        store.close()
    _emit_json({"scope": scope.name, "communities": list(scope.communities), "ingested": summary})
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    store = ZenodoStore.open()
    try:
        summary = embed_records(config=config, store=store, limit=args.limit)
    finally:
        store.close()
    _emit_json(summary)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    filter_payload = json.loads(args.filter) if args.filter else None
    hits = semantic_search(
        config=config,
        query=args.query,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        filter_payload=filter_payload,
    )
    _emit_json(hits)
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    if args.predefined and args.sql:
        message = "Pass either --predefined or --sql, not both"
        raise SystemExit(message)
    params: dict[str, Any] = {}
    for kv in args.param or []:
        if "=" not in kv:
            message = f"--param expects key=value, got {kv!r}"
            raise SystemExit(message)
        key, value = kv.split("=", 1)
        # Try int / float coercion; fall back to string.
        try:
            params[key] = int(value)
        except ValueError:
            try:
                params[key] = float(value)
            except ValueError:
                params[key] = value
    if args.predefined:
        rows = run_predefined(args.predefined, params)
    elif args.sql:
        rows = run_adhoc(args.sql, params)
    else:
        _emit_json({"predefined": sorted(PREDEFINED_QUERIES)})
        return 0
    _emit_json(rows)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    del args
    config = load_config()
    store = ZenodoStore.open()
    try:
        counts = {
            t: store.count(t)
            for t in (
                "records",
                "creators",
                "record_creators",
                "communities",
                "record_communities",
                "files",
                "chunks",
            )
        }
    finally:
        store.close()
    qdrant_count: int | str
    try:
        from src.index.openalex.vector.qdrant_store import QdrantStore

        qdrant_count = QdrantStore(config).count(ZENODO_COLLECTION)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        qdrant_count = f"error: {exc}"
    _emit_json(
        {
            "duckdb_path": str(config.paths.duckdb_path),
            "duckdb_counts": counts,
            "qdrant_collection": ZENODO_COLLECTION,
            "qdrant_points": qdrant_count,
            "scope_default": config.scope.default,
            "epfl_communities": config.scope.epfl_communities,
        },
    )
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    if args.source != "infoscience":
        message = f"unknown discovery source: {args.source!r} (only 'infoscience' is supported)"
        raise SystemExit(message)
    from pathlib import Path

    config = load_config()
    store = ZenodoStore.open()
    try:
        result = discover_from_infoscience(store=store)
        if args.out:
            Path(args.out).write_text(
                json.dumps(
                    {
                        "files_scanned": result.files_scanned,
                        "io_errors": result.io_errors,
                        "files_with_zenodo": result.files_with_zenodo,
                        "distinct_ids": result.distinct_ids,
                        "new_ids": result.new_ids,
                        "overlap_ids": result.overlap_ids,
                        "communities_in_urls": result.communities_in_urls,
                        "file_to_rec": result.file_to_rec,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        ingest_summary: dict[str, Any] | None = None
        if args.ingest and result.new_ids:
            ingest_summary = ingest_by_ids(
                config=config,
                store=store,
                ids=result.new_ids,
                refresh=False,
            )
    finally:
        store.close()

    payload: dict[str, Any] = {
        "source": args.source,
        "files_scanned": result.files_scanned,
        "io_errors": result.io_errors,
        "files_with_zenodo": result.files_with_zenodo,
        "distinct_ids_count": len(result.distinct_ids),
        "overlap_count": len(result.overlap_ids),
        "new_count": len(result.new_ids),
        "communities_in_urls": result.communities_in_urls,
    }
    if args.out:
        payload["output_file"] = args.out
    if ingest_summary is not None:
        payload["ingest"] = {
            "requested": ingest_summary["requested"],
            "fetched": ingest_summary["fetched"],
            "persisted_count": len(ingest_summary["persisted"]),
            "missing_count": len(ingest_summary["missing"]),
            "failed_count": len(ingest_summary["failed"]),
        }
    _emit_json(payload)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "src.index.zenodo.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.index.zenodo")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_discover = sub.add_parser(
        "discover",
        help="Find candidate Zenodo IDs from external sources (e.g. Infoscience)",
    )
    p_discover.add_argument(
        "--source",
        default="infoscience",
        choices=["infoscience"],
        help="Discovery source (only 'infoscience' is supported today)",
    )
    p_discover.add_argument(
        "--out",
        default=None,
        help="Optional path to write the full discovery payload (JSON)",
    )
    p_discover.add_argument(
        "--ingest",
        action="store_true",
        help="After discovery, fetch + persist the new IDs in the same process",
    )
    p_discover.set_defaults(func=_cmd_discover)

    p_ingest = sub.add_parser("ingest", help="Pull Zenodo records into DuckDB")
    p_ingest.add_argument("--scope", default="epfl", help="Scope name (epfl, switzerland, ethz, cern, cern_openlab, all)")
    p_ingest.add_argument("--limit", type=int, default=None, help="Per-community record cap")
    p_ingest.add_argument("--refresh", action="store_true", help="Re-ingest completed communities")
    p_ingest.add_argument(
        "--ids",
        default=None,
        help=(
            "Path to a newline-delimited file of Zenodo record IDs / DOIs / URLs. "
            "When set, --scope is ignored and records are fetched one-by-one via /api/records/{id}."
        ),
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_embed = sub.add_parser("embed", help="Chunk + embed records, push to Qdrant")
    p_embed.add_argument("--limit", type=int, default=None)
    p_embed.set_defaults(func=_cmd_embed)

    p_search = sub.add_parser("search", help="Semantic retrieval (vector + rerank)")
    p_search.add_argument("query", help="Natural-language query")
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.add_argument("--candidate-k", type=int, default=50)
    p_search.add_argument("--filter", default=None, help="JSON dict for Qdrant payload filter")
    p_search.set_defaults(func=_cmd_search)

    p_query = sub.add_parser("query", help="Read-only SQL (predefined or guarded ad-hoc)")
    p_query.add_argument("--predefined", default=None, help=f"One of: {sorted(PREDEFINED_QUERIES)}")
    p_query.add_argument("--sql", default=None, help="Ad-hoc SELECT/WITH query")
    p_query.add_argument("--param", action="append", help="Repeatable key=value param")
    p_query.set_defaults(func=_cmd_query)

    p_status = sub.add_parser("status", help="Show DuckDB + Qdrant counts and paths")
    p_status.set_defaults(func=_cmd_status)

    p_serve = sub.add_parser("serve", help="Run the Zenodo FastAPI app")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8003)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    # `force=True` overrides any handler that an upstream `import` may
    # have attached to the root logger — otherwise our heartbeats can
    # land in /dev/null when something else (logfire, pydantic-ai)
    # captures stderr first.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        force=True,
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:  # noqa: BLE001 — top-level surface for operator clarity
        # DuckDB raises `IOException` when another process holds the
        # file lock. Print the actionable message so an operator
        # doesn't get a silent exit 0 / 1 and wonder what happened.
        message = str(exc)
        if "Could not set lock" in message:
            sys.stderr.write(
                "\nERROR: another Zenodo ingest is already running and "
                "holding the DuckDB lock.\n"
                f"Detail: {message}\n"
                "Wait for it to finish, or `kill` it before retrying.\n",
            )
            return 1
        raise
