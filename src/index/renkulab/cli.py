"""CLI for the RenkuLab index module.

Subcommands:

- `ingest`     — pull projects/groups/users/data_connectors into DuckDB.
- `embed`      — chunk + embed entities, push vectors to Qdrant.
- `search`     — semantic retrieval (vector + RCP rerank).
- `query`      — read-only SQL over DuckDB (predefined or guarded ad-hoc).
- `status`     — print row counts + Qdrant collection sizes + paths.
- `serve`      — run the FastAPI app.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from src.index.renkulab.config import load_config
from src.index.renkulab.embed.pipeline import COLLECTION_BY_ENTITY, embed_entities
from src.index.renkulab.ingest.pipeline import ingest_all
from src.index.renkulab.ingest.scope import resolve_scope
from src.index.renkulab.retrieval.semantic import semantic_search
from src.index.renkulab.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.renkulab.storage.duckdb_store import RenkulabStore

LOGGER = logging.getLogger(__name__)

_VALID_ENTITIES = {
    "projects",
    "groups",
    "users",
    "data_connectors",
    "group_members",
    "project_members",
}


def _emit_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config()
    scope = resolve_scope(args.scope, config)
    only = set(_split_csv(args.only) or []) or None
    if only:
        unknown = only - _VALID_ENTITIES
        if unknown:
            message = (
                f"--only contains unknown entity types: {sorted(unknown)}. "
                f"Known: {sorted(_VALID_ENTITIES)}"
            )
            raise SystemExit(message)
    store = RenkulabStore.open()
    try:
        summary = ingest_all(
            config=config,
            store=store,
            scope=scope,
            limit=args.limit,
            refresh=args.refresh,
            only=only,
        )
    finally:
        store.close()
    _emit_json({"scope": scope.name, "ingested": summary})
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    entity_types = _split_csv(args.entities)
    if entity_types:
        unknown = set(entity_types) - set(COLLECTION_BY_ENTITY)
        if unknown:
            message = (
                f"--entities contains unknown types: {sorted(unknown)}. "
                f"Known: {sorted(COLLECTION_BY_ENTITY)}"
            )
            raise SystemExit(message)
    store = RenkulabStore.open()
    try:
        summary = embed_entities(
            config=config,
            store=store,
            entity_types=entity_types,
            limit=args.limit,
        )
    finally:
        store.close()
    _emit_json(summary)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    filter_payload = json.loads(args.filter) if args.filter else None
    entity_types = _split_csv(args.entities)
    hits = semantic_search(
        config=config,
        query=args.query,
        entity_types=entity_types,
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
    store = RenkulabStore.open()
    try:
        counts = {
            t: store.count(t)
            for t in (
                "projects",
                "groups",
                "users",
                "data_connectors",
                "group_members",
                "project_members",
                "chunks",
            )
        }
    finally:
        store.close()
    qdrant_counts: dict[str, Any] = {}
    try:
        from src.index.openalex.vector.qdrant_store import QdrantStore

        qd = QdrantStore(config)  # type: ignore[arg-type]
        for entity_type, collection in COLLECTION_BY_ENTITY.items():
            try:
                qdrant_counts[collection] = qd.count(collection)
            except Exception as exc:  # noqa: BLE001
                qdrant_counts[collection] = f"error: {exc}"
    except Exception as exc:  # noqa: BLE001
        qdrant_counts = {"_error": str(exc)}
    _emit_json(
        {
            "duckdb_path": str(config.paths.duckdb_path),
            "duckdb_counts": counts,
            "qdrant_collections": qdrant_counts,
            "scope_default": config.scope.default,
        },
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "src.index.renkulab.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="src.index.renkulab")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Pull RenkuLab entities into DuckDB")
    p_ingest.add_argument(
        "--scope",
        default="all",
        help="Scope filter: all, epfl, switzerland",
    )
    p_ingest.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Per-entity record cap",
    )
    p_ingest.add_argument(
        "--refresh",
        action="store_true",
        help="Re-ingest entities already marked complete",
    )
    p_ingest.add_argument(
        "--only",
        default=None,
        help=(
            "Comma-separated entity allow-list. Choices: "
            "projects,groups,users,data_connectors,group_members,project_members"
        ),
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_embed = sub.add_parser("embed", help="Chunk + embed entities, push to Qdrant")
    p_embed.add_argument(
        "--entities",
        default=None,
        help=(
            "Comma-separated entity allow-list. Choices: "
            "projects,groups,users,data_connectors. Default: all four."
        ),
    )
    p_embed.add_argument("--limit", type=int, default=None)
    p_embed.set_defaults(func=_cmd_embed)

    p_search = sub.add_parser("search", help="Semantic retrieval (vector + rerank)")
    p_search.add_argument("query", help="Natural-language query")
    p_search.add_argument(
        "--entities",
        default=None,
        help=(
            "Comma-separated entity allow-list to search. "
            "Default: all four collections (results merged + reranked)."
        ),
    )
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.add_argument("--candidate-k", type=int, default=50)
    p_search.add_argument(
        "--filter",
        default=None,
        help="JSON dict for Qdrant payload filter",
    )
    p_search.set_defaults(func=_cmd_search)

    p_query = sub.add_parser("query", help="Read-only SQL (predefined or guarded ad-hoc)")
    p_query.add_argument(
        "--predefined",
        default=None,
        help=f"One of: {sorted(PREDEFINED_QUERIES)}",
    )
    p_query.add_argument("--sql", default=None, help="Ad-hoc SELECT/WITH query")
    p_query.add_argument("--param", action="append", help="Repeatable key=value param")
    p_query.set_defaults(func=_cmd_query)

    p_status = sub.add_parser("status", help="Show DuckDB + Qdrant counts and paths")
    p_status.set_defaults(func=_cmd_status)

    p_serve = sub.add_parser("serve", help="Run the RenkuLab FastAPI app")
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
