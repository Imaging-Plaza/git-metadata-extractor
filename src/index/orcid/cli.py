"""CLI for the ORCID index module.

Subcommands:

- `discover` — build seed ORCID list (OpenAlex authors + ORCID search).
- `ingest`   — fetch full records, post-filter by scope, persist to DuckDB.
- `embed`    — chunk + embed in-scope rows, push to Qdrant via RCP.
- `search`   — semantic retrieval (vector + RCP rerank).
- `query`    — read-only SQL over DuckDB (predefined or guarded ad-hoc).
- `status`   — counts + paths summary.
- `serve`    — run the FastAPI app on a chosen port.

The `--scope` flag selects the data subtree (`<INDEX_DATA_DIR>/orcid-<scope>/`)
and the Qdrant collection prefix.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from src.index.orcid.config import load_config
from src.index.orcid.models import ALL_ENTITY_TYPES
from src.index.orcid.retrieval.sql import run_adhoc, run_predefined
from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore

LOGGER = logging.getLogger(__name__)


VALID_ENTITIES = set(ALL_ENTITY_TYPES)


def _split_entities(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    unknown = [p for p in parts if p not in VALID_ENTITIES]
    if unknown:
        message = f"Unknown entity types: {unknown}. Known: {sorted(VALID_ENTITIES)}"
        raise SystemExit(message)
    return parts


def _emit_json(data: object) -> None:
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _apply_scope_env(scope: str) -> None:
    """Propagate `--scope` to env so paths.get_orcid_paths picks it up."""
    os.environ["INDEX_ORCID_SCOPE"] = scope


def _cmd_discover(args: argparse.Namespace) -> int:
    from src.index.orcid.ingest.discover import discover_seeds

    _apply_scope_env(args.scope)
    config = load_config(scope=args.scope)
    store = OrcidDuckDBStore.open(scope=args.scope)
    summary = discover_seeds(config=config, store=store, source=args.source)
    _emit_json({"scope": args.scope, "seeded": summary})
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    from src.index.orcid.ingest.persons import ingest_persons

    _apply_scope_env(args.scope)
    config = load_config(scope=args.scope)
    store = OrcidDuckDBStore.open(scope=args.scope)
    summary = ingest_persons(
        config=config,
        store=store,
        scope=args.scope,
        limit=args.limit,
    )
    _emit_json({"scope": args.scope, "ingested": summary})
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    from src.index.orcid.embed.pipeline import embed_entities

    _apply_scope_env(args.scope)
    entities = _split_entities(args.entities)
    config = load_config(scope=args.scope)
    config.require_rcp()
    store = OrcidDuckDBStore.open(scope=args.scope)
    summary = embed_entities(
        config=config,
        store=store,
        entity_types=entities,
        limit=args.limit,
    )
    _emit_json({"scope": args.scope, "embedded": summary})
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    from src.index.orcid.retrieval.semantic import semantic_search

    _apply_scope_env(args.scope)
    config = load_config(scope=args.scope)
    config.require_rcp()
    hits = semantic_search(
        config=config,
        query=args.query,
        entity_type=args.entity,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
    )
    _emit_json(hits)
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    _apply_scope_env(args.scope)
    params: dict[str, object] = {}
    for raw in args.param or []:
        if "=" not in raw:
            message = f"--param must be key=value, got {raw!r}"
            raise SystemExit(message)
        key, value = raw.split("=", 1)
        params[key] = int(value) if value.isdigit() else value
    store = OrcidDuckDBStore.open(scope=args.scope)
    if args.predefined:
        rows = run_predefined(args.predefined, params, store=store)
    elif args.sql:
        rows = run_adhoc(args.sql, params, store=store)
    else:
        message = "Pass --predefined NAME or a positional SQL string"
        raise SystemExit(message)
    _emit_json(rows)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    _apply_scope_env(args.scope)
    config = load_config(scope=args.scope)
    store = OrcidDuckDBStore.open(scope=args.scope)
    counts = {
        "seeds": store.count("seeds"),
        "persons": store.count("persons"),
        "employments": store.count("employments"),
        "educations": store.count("educations"),
        "chunks": store.count("chunks"),
    }
    _emit_json(
        {
            "scope": args.scope,
            "duckdb_path": str(config.paths.duckdb_path),
            "qdrant_url": config.qdrant.url,
            "qdrant_collections": [
                config.paths.collection_name(e) for e in ALL_ENTITY_TYPES
            ],
            "counts": counts,
        },
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    _apply_scope_env(args.scope)
    uvicorn.run(
        "src.index.orcid.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _add_scope_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope",
        choices=["epfl", "switzerland"],
        default="epfl",
        help="Data subtree + Qdrant collection prefix to operate on",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="src.index.orcid.cli",
        description="ORCID ingestion + RAG over EPFL → Switzerland",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_d = sub.add_parser("discover", help="Build seed ORCID list")
    _add_scope_arg(p_d)
    p_d.add_argument("--source", choices=["openalex", "orcid_search", "both"], default=None)
    p_d.set_defaults(func=_cmd_discover)

    p_i = sub.add_parser("ingest", help="Fetch full records + persist")
    _add_scope_arg(p_i)
    p_i.add_argument("--limit", type=int, default=None)
    p_i.set_defaults(func=_cmd_ingest)

    p_e = sub.add_parser("embed", help="Embed in-scope rows into Qdrant via RCP")
    _add_scope_arg(p_e)
    p_e.add_argument("--entities", default=",".join(ALL_ENTITY_TYPES))
    p_e.add_argument("--limit", type=int, default=None)
    p_e.set_defaults(func=_cmd_embed)

    p_s = sub.add_parser("search", help="Semantic retrieval (vector + rerank)")
    _add_scope_arg(p_s)
    p_s.add_argument("query")
    p_s.add_argument("--entity", default="persons", choices=list(ALL_ENTITY_TYPES))
    p_s.add_argument("--top-k", type=int, default=10)
    p_s.add_argument("--candidate-k", type=int, default=50)
    p_s.set_defaults(func=_cmd_search)

    p_q = sub.add_parser("query", help="Read-only SQL over DuckDB")
    _add_scope_arg(p_q)
    p_q.add_argument("sql", nargs="?", help="Ad-hoc SELECT/WITH (omit if --predefined)")
    p_q.add_argument("--predefined")
    p_q.add_argument("--param", action="append")
    p_q.set_defaults(func=_cmd_query)

    p_st = sub.add_parser("status", help="Show counts + paths")
    _add_scope_arg(p_st)
    p_st.set_defaults(func=_cmd_status)

    p_v = sub.add_parser("serve", help="Run the FastAPI app")
    _add_scope_arg(p_v)
    p_v.add_argument("--host", default="0.0.0.0")  # noqa: S104
    p_v.add_argument("--port", type=int, default=8002)
    p_v.add_argument("--reload", action="store_true")
    p_v.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
