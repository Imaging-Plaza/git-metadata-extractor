"""CLI for the GitHub index module.

Subcommands:

- `ingest`          — fetch GitHub repo metadata + README into DuckDB.
- `embed`           — chunk + embed unembedded repos, push vectors to Qdrant.
- `rebuild-qdrant`  — re-derive Qdrant points from the existing `chunks` table
                      (recovery path after a Qdrant wipe; does not touch DuckDB).
- `search`          — semantic retrieval (vector + RCP rerank).
- `query`           — read-only SQL over DuckDB (predefined or guarded ad-hoc).
- `status`          — print row counts + Qdrant collection size + paths.
- `serve`           — run the FastAPI app.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from src.index.github_repos.config import load_config
from src.index.github_repos.embed.pipeline import (
    GITHUB_REPOS_COLLECTION,
    embed_repos,
    rebuild_qdrant_from_chunks,
)
from src.index.github_repos.ingest.repos import ingest_repos
from src.index.github_repos.ingest.scope import merge_openalex_repos, resolve_scope
from src.index.github_repos.retrieval.semantic import semantic_search
from src.index.github_repos.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.github_repos.storage.duckdb_store import GitHubReposStore

LOGGER = logging.getLogger(__name__)


def _emit_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_github()
    scope = resolve_scope(args.scope, config)
    if args.repos:
        # CLI-supplied repos extend the scope without touching the YAML seed.
        scope.repos = list(dict.fromkeys([*scope.repos, *args.repos]))
    if args.repos_file:
        from pathlib import Path

        path = Path(args.repos_file)
        lines = [
            ln.strip()
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        scope.repos = list(dict.fromkeys([*scope.repos, *lines]))
    if args.from_openalex:
        from src.index.openalex.paths import get_openalex_paths

        scope = merge_openalex_repos(
            scope,
            openalex_db_path=get_openalex_paths().duckdb_path,
        )
    if not scope.repos:
        message = (
            f"scope={scope.name} resolved to zero repos. "
            "Edit config/index/github.yaml under `scope.seeds.<name>` "
            "or pass --repos / --repos-file."
        )
        raise SystemExit(message)
    store = GitHubReposStore.open()
    try:
        summary = ingest_repos(
            config=config,
            store=store,
            scope=scope,
            limit=args.limit,
        )
    finally:
        store.close()
    _emit_json({"scope": scope.name, "repos_in_scope": len(scope.repos), **summary})
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    store = GitHubReposStore.open()
    try:
        summary = embed_repos(config=config, store=store, limit=args.limit)
    finally:
        store.close()
    _emit_json(summary)
    return 0


def _cmd_rebuild_qdrant(args: argparse.Namespace) -> int:
    del args
    config = load_config()
    config.require_rcp()
    store = GitHubReposStore.open()
    try:
        summary = rebuild_qdrant_from_chunks(config=config, store=store)
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
    store = GitHubReposStore.open()
    try:
        counts = {t: store.count(t) for t in ("repos", "chunks")}
    finally:
        store.close()
    qdrant_count: int | str
    try:
        from src.index.openalex.vector.qdrant_store import QdrantStore

        qdrant_count = QdrantStore(config).count(GITHUB_REPOS_COLLECTION)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        qdrant_count = f"error: {exc}"
    _emit_json(
        {
            "duckdb_path": str(config.paths.duckdb_path),
            "duckdb_counts": counts,
            "qdrant_collection": GITHUB_REPOS_COLLECTION,
            "qdrant_points": qdrant_count,
            "scope_active": config.scope.active,
            "scope_seed_sizes": {k: len(v) for k, v in config.scope.seeds.items()},
        },
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "src.index.github_repos.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _split_repos(raw: str) -> list[str]:
    return [r.strip() for r in raw.split(",") if r.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.index.github_repos")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Fetch GitHub repo metadata + README into DuckDB")
    p_ingest.add_argument("--scope", default="epfl", help="Scope name (key in scope.seeds)")
    p_ingest.add_argument(
        "--repos",
        type=_split_repos,
        default=None,
        help="Extra owner/name list, comma-separated (extends the scope without editing YAML)",
    )
    p_ingest.add_argument(
        "--repos-file",
        default=None,
        help="Path to a newline-delimited file of owner/name entries (# comments + blank lines ignored)",
    )
    p_ingest.add_argument(
        "--from-openalex",
        action="store_true",
        help="Augment the scope with distinct repos from openalex.work_github_urls",
    )
    p_ingest.add_argument("--limit", type=int, default=None, help="Cap repos processed this run")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_embed = sub.add_parser("embed", help="Chunk + embed repos, push to Qdrant")
    p_embed.add_argument("--limit", type=int, default=None)
    p_embed.set_defaults(func=_cmd_embed)

    p_rebuild = sub.add_parser(
        "rebuild-qdrant",
        help="Re-derive Qdrant points from the existing chunks table (post-wipe recovery)",
    )
    p_rebuild.set_defaults(func=_cmd_rebuild_qdrant)

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

    p_serve = sub.add_parser("serve", help="Run the GitHub FastAPI app")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8004)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
