"""CLI for the EPFL Graph disciplines index.

Subcommands:

- ``ingest`` — walk graphai's ontology tree and persist categories to DuckDB.
- ``embed``  — push embeddings of every category into Qdrant.
- ``search`` — semantic retrieval (vector + optional RCP rerank).
- ``status`` — row counts + Qdrant collection info + paths.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from src.index.epfl_graph.config import load_config
from src.index.epfl_graph.embed.pipeline import (
    EPFL_GRAPH_COLLECTION,
    embed_disciplines,
)
from src.index.epfl_graph.ingest.download import ingest_tree
from src.index.epfl_graph.ingest.wikidata_qids import fetch_wikidata_qids
from src.index.epfl_graph.ingest.wikipedia_extracts import (
    fetch_wikipedia_extracts,
    rebuild_embedding_texts,
)
from src.index.epfl_graph.retrieval.semantic import semantic_search
from src.index.epfl_graph.storage.duckdb_store import EpflGraphStore

LOGGER = logging.getLogger(__name__)


def _emit_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config()
    ingested = ingest_tree(config, limit=args.limit)
    _emit_json({"ingested": ingested, "duckdb_path": str(config.paths.duckdb_path)})
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    store = EpflGraphStore.open(config.paths.duckdb_path)
    try:
        embedded = embed_disciplines(config, store, limit=args.limit)
    finally:
        store.close()
    _emit_json(
        {
            "embedded": embedded,
            "collection": EPFL_GRAPH_COLLECTION,
            "qdrant_url": config.qdrant.url,
        },
    )
    return 0


def _cmd_enrich_wikipedia(args: argparse.Namespace) -> int:
    """Fetch missing Wikipedia extracts and rebuild embedding_text."""
    config = load_config()
    store = EpflGraphStore.open(config.paths.duckdb_path)
    try:
        fetched = fetch_wikipedia_extracts(config, store, limit=args.limit)
        rebuilt = rebuild_embedding_texts(config, store)
    finally:
        store.close()
    _emit_json(
        {
            "wikipedia_extracts_fetched": fetched,
            "embedding_texts_rebuilt": rebuilt,
            "duckdb_path": str(config.paths.duckdb_path),
        },
    )
    return 0


def _cmd_enrich_wikidata(args: argparse.Namespace) -> int:
    """Fetch missing Wikidata QIDs for categories via Wikipedia pageprops."""
    config = load_config()
    store = EpflGraphStore.open(config.paths.duckdb_path)
    try:
        fetched = fetch_wikidata_qids(config, store, limit=args.limit)
    finally:
        store.close()
    _emit_json(
        {
            "wikidata_qids_fetched": fetched,
            "duckdb_path": str(config.paths.duckdb_path),
        },
    )
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    config = load_config()
    config.require_rcp()
    hits = semantic_search(
        config=config,
        query=args.query,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        min_depth=args.min_depth,
        rerank=not args.no_rerank,
    )
    _emit_json(hits)
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    config = load_config()
    store = EpflGraphStore.open(config.paths.duckdb_path)
    try:
        summary = {
            "duckdb_path": str(config.paths.duckdb_path),
            "categories": store.count_categories(),
            "category_concepts": store.count_concepts(),
            "qdrant_url": config.qdrant.url,
            "qdrant_collection": EPFL_GRAPH_COLLECTION,
            "min_depth_for_embedding": config.filter.min_depth,
        }
    finally:
        store.close()
    _emit_json(summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.index.epfl_graph",
        description="EPFL Graph disciplines RAG indexer",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Walk the ontology tree into DuckDB")
    p_ingest.add_argument("--limit", type=int, default=None)
    p_ingest.set_defaults(func=_cmd_ingest)

    p_embed = sub.add_parser("embed", help="Embed disciplines into Qdrant")
    p_embed.add_argument("--limit", type=int, default=None)
    p_embed.set_defaults(func=_cmd_embed)

    p_enrich = sub.add_parser(
        "enrich-wikipedia",
        help="Fill missing Wikipedia extracts and rebuild embedding_text",
    )
    p_enrich.add_argument("--limit", type=int, default=None)
    p_enrich.set_defaults(func=_cmd_enrich_wikipedia)

    p_qids = sub.add_parser(
        "enrich-wikidata",
        help="Fill missing wikidata_qid via Wikipedia pageprops (prop=pageprops)",
    )
    p_qids.add_argument("--limit", type=int, default=None)
    p_qids.set_defaults(func=_cmd_enrich_wikidata)

    p_search = sub.add_parser("search", help="Semantic search over disciplines")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.add_argument("--candidate-k", type=int, default=50)
    p_search.add_argument("--min-depth", type=int, default=None)
    p_search.add_argument("--no-rerank", action="store_true")
    p_search.set_defaults(func=_cmd_search)

    p_status = sub.add_parser("status", help="Print index status")
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
