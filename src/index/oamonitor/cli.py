"""CLI for the OAM-CH RAG indexer.

Subcommands:

- ``ingest``  — stream documents from the OAM Mongo-proxy into DuckDB.
- ``embed``   — push embedding_text for each entity into a per-entity Qdrant collection.
- ``search``  — semantic retrieval (vector + RCP rerank) over one entity collection.
- ``status``  — DuckDB row counts + Qdrant collection counts.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from src.index.oamonitor.config import load_config
from src.index.oamonitor.embed.pipeline import OAM_COLLECTIONS, embed_entities
from src.index.oamonitor.ingest.journals import ingest_journals
from src.index.oamonitor.ingest.oamonitor_client import OamonitorClient
from src.index.oamonitor.ingest.organisations import ingest_organisations
from src.index.oamonitor.ingest.publications import ingest_publications
from src.index.oamonitor.ingest.publishers import ingest_publishers
from src.index.oamonitor.retrieval.semantic import semantic_search
from src.index.oamonitor.storage.duckdb_store import ENTITY_TABLES, OamonitorStore

LOGGER = logging.getLogger(__name__)


def _emit_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _split_entities(value: str | None, *, valid: tuple[str, ...]) -> list[str]:
    if not value:
        return list(valid)
    requested = [v.strip() for v in value.split(",") if v.strip()]
    unknown = sorted(set(requested) - set(valid))
    if unknown:
        message = f"Unsupported entity types: {unknown}; expected subset of {list(valid)}"
        raise SystemExit(message)
    return requested


_ENTITY_INGESTERS = {
    "journals": ingest_journals,
    "publications": ingest_publications,
    "publishers": ingest_publishers,
    "organisations": ingest_organisations,
}


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config()
    entities = _split_entities(args.entities, valid=ENTITY_TABLES)
    client = OamonitorClient(config)
    store = OamonitorStore.open(config.paths.duckdb_path)
    filter_payload: dict[str, Any] | None = None
    if args.filter:
        filter_payload = json.loads(args.filter)
        if not isinstance(filter_payload, dict):
            message = "--filter must decode to a JSON object"
            raise SystemExit(message)
    summary: dict[str, int] = {}
    try:
        for entity in entities:
            ingester = _ENTITY_INGESTERS[entity]
            summary[entity] = ingester(
                config=config,
                client=client,
                store=store,
                limit=args.limit,
                skip=args.skip,
                filter_payload=filter_payload,
            )
    finally:
        store.close()
    _emit_json(
        {
            "ingested": summary,
            "skip": args.skip,
            "filter": filter_payload,
            "duckdb_path": str(config.paths.duckdb_path),
        },
    )
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    entities = _split_entities(args.entities, valid=ENTITY_TABLES)
    store = OamonitorStore.open(config.paths.duckdb_path)
    try:
        summary = embed_entities(
            config=config, store=store, entities=entities, limit=args.limit,
        )
    finally:
        store.close()
    _emit_json({"embedded": summary})
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    filter_payload = json.loads(args.filter) if args.filter else None
    hits = semantic_search(
        config=config,
        query=args.query,
        entity_type=args.entity,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        filter_payload=filter_payload,
    )
    _emit_json({"query": args.query, "entity": args.entity, "hits": hits})
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    config = load_config()
    store = OamonitorStore.open(config.paths.duckdb_path)
    counts = {table: store.count(table) for table in ENTITY_TABLES}
    store.close()
    summary: dict[str, Any] = {
        "duckdb_path": str(config.paths.duckdb_path),
        "duckdb_counts": counts,
        "qdrant_url": config.qdrant.url,
        "qdrant_collections": OAM_COLLECTIONS,
    }
    try:
        from src.index.openalex.vector.qdrant_store import QdrantStore  # noqa: PLC0415

        qdrant = QdrantStore(config)  # type: ignore[arg-type]
        summary["qdrant_collection_counts"] = {
            entity: qdrant.count(collection)
            for entity, collection in OAM_COLLECTIONS.items()
        }
    except Exception as exc:  # noqa: BLE001
        summary["qdrant_error"] = str(exc)
    _emit_json(summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.index.oamonitor",
        description="Open Access Monitor CH RAG indexer",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser(
        "ingest",
        help="Stream documents from OAM-CH into DuckDB (per entity collection)",
    )
    p_ingest.add_argument(
        "--entities",
        default=None,
        help="Comma-separated subset of journals,publications,publishers,organisations (default: all)",
    )
    p_ingest.add_argument("--limit", type=int, default=None)
    p_ingest.add_argument(
        "--skip",
        type=int,
        default=0,
        help=(
            "Server-side offset to start the cursor from. Use with --limit "
            "to run batched ingests, e.g. `--skip 0 --limit 100000` then "
            "`--skip 100000 --limit 100000`."
        ),
    )
    p_ingest.add_argument(
        "--filter",
        default=None,
        help=(
            "JSON MongoDB filter forwarded as the `filter` field of the "
            "upstream `find` command. Example for EPFL-only publications: "
            "`--filter '{\"organisations._id\":\"https://ror.org/02s376052\"}'`."
        ),
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_embed = sub.add_parser(
        "embed",
        help="Embed each entity table into its per-entity Qdrant collection",
    )
    p_embed.add_argument("--entities", default=None)
    p_embed.add_argument("--limit", type=int, default=None)
    p_embed.set_defaults(func=_cmd_embed)

    p_search = sub.add_parser("search", help="Semantic search over one OAM entity")
    p_search.add_argument("query")
    p_search.add_argument(
        "--entity",
        choices=list(ENTITY_TABLES),
        default="journals",
    )
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.add_argument("--candidate-k", type=int, default=50)
    p_search.add_argument(
        "--filter",
        default=None,
        help="JSON-encoded Qdrant filter payload",
    )
    p_search.set_defaults(func=_cmd_search)

    p_status = sub.add_parser("status", help="Show row + Qdrant collection counts")
    p_status.set_defaults(func=_cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
