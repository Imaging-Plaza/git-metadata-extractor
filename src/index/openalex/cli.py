"""CLI for the OpenAlex index module.

Subcommands:

- `ingest`        — pull OpenAlex entities into DuckDB.
- `find-github`   — discover GitHub-URL-mentioning Works (Swiss/EPFL test set).
- `query`         — read-only SQL over DuckDB (predefined or guarded ad-hoc).
- `embed`         — embed DuckDB rows into Qdrant via RCP. (See embed module.)
- `rebuild-qdrant` — re-push existing DuckDB `chunks` rows into Qdrant
  (re-embedding their text via RCP). Used after a Qdrant wipe.
- `search`        — semantic retrieval (vector + rerank). (See retrieval.)
- `serve`         — run the FastAPI app on a chosen port.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import TYPE_CHECKING

from src.index.openalex.config import load_config
from src.index.openalex.ingest.authors import ingest_authors
from src.index.openalex.ingest.concepts import ingest_concepts
from src.index.openalex.ingest.github_discovery import discover_github_works
from src.index.openalex.ingest.github_extract import extract_for_persisted_works
from src.index.openalex.ingest.institutions import ingest_institutions
from src.index.openalex.ingest.scope import resolve_scope
from src.index.openalex.ingest.sources import ingest_sources
from src.index.openalex.ingest.topics import ingest_topics
from src.index.openalex.ingest.works import ingest_works
from src.index.openalex.retrieval.sql import run_adhoc, run_predefined
from src.index.openalex.storage.duckdb_store import OpenAlexStore

if TYPE_CHECKING:
    pass

LOGGER = logging.getLogger(__name__)

ENTITY_INGESTERS = {
    "works": ingest_works,
    "authors": ingest_authors,
    "institutions": ingest_institutions,
    "sources": ingest_sources,
    "topics": ingest_topics,
    "concepts": ingest_concepts,
}


def _split_entities(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    unknown = [p for p in parts if p not in ENTITY_INGESTERS]
    if unknown:
        message = f"Unknown entity types: {unknown}. Known: {sorted(ENTITY_INGESTERS)}"
        raise SystemExit(message)
    return parts


def _emit_json(rows: list[dict] | dict) -> None:
    json.dump(rows, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_ingest()
    scope = resolve_scope(args.scope, config)
    entities = _split_entities(args.entities)
    store = OpenAlexStore.open()
    summary: dict[str, int] = {}
    scope_filters = {
        "works": scope.works,
        "authors": scope.authors,
        "institutions": scope.institutions,
        "sources": scope.sources,
        "topics": {},
        "concepts": {},
    }
    for entity in entities:
        ingester = ENTITY_INGESTERS[entity]
        filters = scope_filters[entity]
        kwargs = {
            "config": config,
            "store": store,
            "filters": filters,
            "limit": args.limit,
        }
        # Topics/concepts don't take a meaningful scope filter — pass through.
        summary[entity] = ingester(**kwargs)
    _emit_json({"scope": args.scope, "ingested": summary})
    return 0


def _cmd_find_github(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_ingest()
    scope = resolve_scope(args.scope, config)
    store = OpenAlexStore.open()
    seen, persisted = discover_github_works(
        config=config,
        store=store,
        scope_filter=scope.works,
        mode=args.search,
        limit=args.limit,
    )
    scanned, urls = extract_for_persisted_works(store)
    distinct = store.connect().execute(
        "SELECT COUNT(DISTINCT normalized_url) FROM work_github_urls"
    ).fetchone()
    _emit_json(
        {
            "scope": args.scope,
            "search": args.search,
            "works_seen": seen,
            "works_persisted": persisted,
            "abstracts_scanned": scanned,
            "urls_persisted_this_run": urls,
            "distinct_normalized_urls": int(distinct[0]) if distinct else 0,
        },
    )
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    params: dict[str, object] = {}
    for raw in args.param or []:
        if "=" not in raw:
            message = f"--param must be key=value, got {raw!r}"
            raise SystemExit(message)
        key, value = raw.split("=", 1)
        # Best-effort numeric coercion — predefined queries take ints.
        if value.isdigit():
            params[key] = int(value)
        else:
            params[key] = value
    if args.predefined:
        rows = run_predefined(args.predefined, params)
    elif args.sql:
        rows = run_adhoc(args.sql, params)
    else:
        raise SystemExit("Pass --predefined NAME or a positional SQL string")
    _emit_json(rows)
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    from src.index.openalex.embed.pipeline import embed_entities

    entities = _split_entities(args.entities)
    config = load_config()
    config.require_rcp()
    store = OpenAlexStore.open()
    summary = embed_entities(
        config=config,
        store=store,
        entity_types=entities,
        limit=args.limit,
    )
    _emit_json({"embedded": summary})
    return 0


def _cmd_rebuild_qdrant(args: argparse.Namespace) -> int:
    from src.index.openalex.embed.pipeline import rebuild_qdrant_from_chunks

    entities = _split_entities(args.entities)
    config = load_config()
    config.require_rcp()
    store = OpenAlexStore.open()
    summary = rebuild_qdrant_from_chunks(
        config=config,
        store=store,
        entity_types=entities,
    )
    _emit_json({"rebuilt": summary})
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    from src.index.openalex.retrieval.semantic import semantic_search

    config = load_config()
    config.require_rcp()
    hits = semantic_search(
        config=config,
        query=args.query,
        entity_type=args.entity,
        top_k=args.top_k,
    )
    _emit_json(hits)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "src.index.openalex.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="src.index.openalex.cli",
        description="OpenAlex ingestion + RAG over EPFL/Switzerland",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Pull OpenAlex entities into DuckDB")
    p_ingest.add_argument("--scope", choices=["epfl", "switzerland"], required=True)
    p_ingest.add_argument(
        "--entities",
        default="works,authors,institutions,sources,topics,concepts",
    )
    p_ingest.add_argument("--limit", type=int, default=None)
    p_ingest.set_defaults(func=_cmd_ingest)

    p_gh = sub.add_parser(
        "find-github",
        help="Discover Swiss/EPFL Works mentioning github.com URLs",
    )
    p_gh.add_argument("--scope", choices=["epfl", "switzerland"], required=True)
    p_gh.add_argument(
        "--search",
        choices=["fulltext", "default", "both"],
        default="both",
    )
    p_gh.add_argument("--limit", type=int, default=None)
    p_gh.set_defaults(func=_cmd_find_github)

    p_q = sub.add_parser("query", help="Read-only SQL over the DuckDB dump")
    p_q.add_argument(
        "sql",
        nargs="?",
        help="Ad-hoc SELECT/WITH query (omit if --predefined)",
    )
    p_q.add_argument(
        "--predefined",
        help="Run a predefined named query",
    )
    p_q.add_argument(
        "--param",
        action="append",
        help="key=value for predefined queries (repeatable)",
    )
    p_q.set_defaults(func=_cmd_query)

    p_e = sub.add_parser("embed", help="Embed DuckDB rows into Qdrant via RCP")
    p_e.add_argument(
        "--entities",
        default="works,authors,institutions,sources,topics,concepts",
    )
    p_e.add_argument("--limit", type=int, default=None)
    p_e.set_defaults(func=_cmd_embed)

    p_r = sub.add_parser(
        "rebuild-qdrant",
        help=(
            "Re-push existing DuckDB chunks into Qdrant (re-embeds chunks.text). "
            "Use after a Qdrant wipe. Does not modify DuckDB."
        ),
    )
    p_r.add_argument(
        "--entities",
        default="works,authors,institutions,sources,topics,concepts",
    )
    p_r.set_defaults(func=_cmd_rebuild_qdrant)

    p_s = sub.add_parser("search", help="Semantic retrieval (vector + rerank)")
    p_s.add_argument("query")
    p_s.add_argument("--entity", default="works")
    p_s.add_argument("--top-k", type=int, default=10)
    p_s.set_defaults(func=_cmd_search)

    p_v = sub.add_parser("serve", help="Run the FastAPI app")
    p_v.add_argument("--host", default="0.0.0.0")  # noqa: S104
    p_v.add_argument("--port", type=int, default=8001)
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
