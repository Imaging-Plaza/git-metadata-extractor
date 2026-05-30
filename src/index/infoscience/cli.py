"""Click-based CLI for the Infoscience indexer.

Run via `python -m src.index.infoscience <subcommand>`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import click

from . import discover as discover_stage
from . import extract_matches as extract_matches_stage
from . import extract_relations as extract_relations_stage
from . import fetch_related as fetch_related_stage
from . import text_fetch as text_fetch_stage
from .config import DEFAULT_CONFIG_PATH, load_config
from .paths import (
    discover_state_path,
    infoscience_data_dir,
    matches_path,
    relations_path,
)

# `build` (LanceDB writes) and `pipeline` (LanceDB reads) are imported
# lazily inside their command bodies so the lighter stages don't require
# lancedb to be installed/loadable.

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


@click.group(help="Infoscience EPFL harvest + RAG indexer.")
@click.option("--config", "config_path", type=click.Path(path_type=Path),
              default=None, help=f"Path to YAML config (default {DEFAULT_CONFIG_PATH}).")
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging.")
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[Path], verbose: bool) -> None:
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_path)


@cli.command()
@click.option("--terms", default=None,
              help="Comma-separated Solr fulltext terms (overrides config).")
@click.option("--limit", type=int, default=None, help="Stop after N new items.")
@click.pass_context
def discover(ctx: click.Context, terms: Optional[str], limit: Optional[int]) -> None:
    """Solr fulltext search → raw item JSON dumps."""
    cfg = ctx.obj["config"]
    term_list = [t.strip() for t in terms.split(",")] if terms else None
    summary = discover_stage.run(cfg, terms=term_list, limit=limit)
    click.echo(json.dumps(summary, indent=2))


@cli.command("fetch-text")
@click.option("--refresh", is_flag=True, help="Re-download even if file exists.")
@click.pass_context
def fetch_text(ctx: click.Context, refresh: bool) -> None:
    """Download TEXT bundle plaintext for each discovered item."""
    cfg = ctx.obj["config"]
    summary = text_fetch_stage.run(cfg, refresh=refresh)
    click.echo(json.dumps(summary, indent=2))


@cli.command("extract-matches")
@click.pass_context
def extract_matches(ctx: click.Context) -> None:
    """Regex-extract GitHub/HuggingFace URLs from fetched text."""
    cfg = ctx.obj["config"]
    summary = extract_matches_stage.run(cfg)
    click.echo(json.dumps(summary, indent=2))


@cli.command("extract-relations")
@click.pass_context
def extract_relations(ctx: click.Context) -> None:
    """Pull Person/Org authority UUIDs from matched articles."""
    cfg = ctx.obj["config"]
    summary = extract_relations_stage.run(cfg)
    click.echo(json.dumps(summary, indent=2))


@cli.command("fetch-related")
@click.option("--type", "kind", type=click.Choice(["person", "org", "all"]),
              default="all")
@click.option("--refresh", is_flag=True)
@click.pass_context
def fetch_related(ctx: click.Context, kind: str, refresh: bool) -> None:
    """Fetch raw JSON for each Person/OrgUnit referenced by matched articles."""
    cfg = ctx.obj["config"]
    summary = fetch_related_stage.run(cfg, kind=kind, refresh=refresh)
    click.echo(json.dumps(summary, indent=2))


@cli.command()
@click.option("--scope", type=click.Choice(["chunks", "articles", "persons", "orgs", "all"]),
              default="all")
@click.option("--batch", type=int, default=None, help="Override embed batch size.")
@click.pass_context
def embed(ctx: click.Context, scope: str, batch: Optional[int]) -> None:
    """Chunk, embed, and populate the LanceDB tables."""
    from . import build as build_stage  # lazy: requires lancedb

    cfg = ctx.obj["config"]
    if batch is not None:
        cfg.rcp.batch_size = batch
    summary = build_stage.run(cfg, scope=scope)
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command("query")
@click.argument("text")
@click.option("--target", type=click.Choice(["chunks", "articles", "persons", "organizations"]),
              default="chunks")
@click.option("--where", default=None,
              help="ChromaDB filter as JSON, e.g. '{\"year\": {\"$gte\": 2022}, \"has_github_match\": true}'.")
@click.option("--top-k", type=int, default=50)
@click.option("--top-n", type=int, default=10)
@click.option("--mode", type=click.Choice(["hybrid", "vector-only", "lexical", "filter-only"]),
              default="hybrid")
@click.option("--no-rerank", is_flag=True, help="Force vector-only mode.")
@click.option("--with-authors", is_flag=True)
@click.option("--with-orgs", is_flag=True)
@click.pass_context
def query_cmd(
    ctx: click.Context,
    text: str,
    target: str,
    where: Optional[str],
    top_k: int,
    top_n: int,
    mode: str,
    no_rerank: bool,
    with_authors: bool,
    with_orgs: bool,
) -> None:
    """Query the index. Filter → vector → rerank by default."""
    from .pipeline import query  # lazy: requires chromadb

    cfg = ctx.obj["config"]
    where_dict = json.loads(where) if where else None
    if no_rerank and mode == "hybrid":
        mode = "vector-only"
    result = asyncio.run(query(
        cfg, text, target=target, where=where_dict, top_k=top_k, top_n=top_n,
        mode=mode, with_authors=with_authors, with_orgs=with_orgs,
    ))
    click.echo(json.dumps({
        "target": result.target,
        "rows": result.rows,
        "related_persons": result.related_persons,
        "related_organizations": result.related_organizations,
    }, indent=2, default=str))


@cli.command("ingest-duckdb")
@click.option(
    "--links-dump",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional path to a scripts/dump_link_articles.py output to merge "
         "into article_links.",
)
@click.pass_context
def ingest_duckdb(ctx: click.Context, links_dump: Optional[Path]) -> None:
    """Ingest raw/{items,persons,organizations}/*.json into the DuckDB store."""
    from .storage import InfoscienceStore
    from .storage.ingest_raw import ingest_all

    store = InfoscienceStore.open()
    try:
        summary = ingest_all(store, links_dump=links_dump)
    finally:
        store.close()
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show counts and paths for each stage."""
    cfg = ctx.obj["config"]
    data_dir = infoscience_data_dir()
    raw = data_dir / "raw"
    counts = {
        "data_dir": str(data_dir),
        "raw_items": len(list((raw / "items").glob("*.json"))) if (raw / "items").exists() else 0,
        "raw_persons": len(list((raw / "persons").glob("*.json"))) if (raw / "persons").exists() else 0,
        "raw_organizations": len(list((raw / "organizations").glob("*.json"))) if (raw / "organizations").exists() else 0,
        "text_files": len(list((data_dir / "text").glob("*.txt"))) if (data_dir / "text").exists() else 0,
        "matches_path": str(matches_path()) if matches_path().exists() else None,
        "relations_path": str(relations_path()) if relations_path().exists() else None,
        "discover_state": str(discover_state_path()) if discover_state_path().exists() else None,
        "config_summary": {
            "embedding_model": cfg.rcp.embedding_model,
            "embedding_dim": cfg.rcp.embedding_dim,
            "reranker_model": cfg.rcp.reranker_model,
            "filter_terms": cfg.filter.terms,
        },
    }
    try:
        from .store import ALL_COLLECTIONS, QdrantStore  # lazy: requires qdrant-client
        store = QdrantStore.from_config(cfg)
        counts["qdrant_url"] = cfg.qdrant.url
        counts["qdrant_collections"] = {
            name: store.collection_count(name) for name in ALL_COLLECTIONS
        }
    except Exception as exc:
        counts["qdrant_error"] = str(exc)
    try:
        from .paths import duckdb_path
        from .storage import InfoscienceStore
        if duckdb_path().exists():
            ddb = InfoscienceStore.open()
            try:
                counts["duckdb_path"] = str(duckdb_path())
                counts["duckdb_counts"] = {
                    t: ddb.count(t)
                    for t in (
                        "articles",
                        "persons",
                        "organizations",
                        "article_persons",
                        "article_orgs",
                        "article_links",
                        "chunks",
                    )
                }
            finally:
                ddb.close()
        else:
            counts["duckdb_path"] = None
    except Exception as exc:
        counts["duckdb_error"] = str(exc)
    click.echo(json.dumps(counts, indent=2, default=str))


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
