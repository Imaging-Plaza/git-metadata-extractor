"""CLI for the HuggingFace index module.

Subcommands:

- `ingest`         — fetch metadata + READMEs for the configured seed.
- `discover-orgs`  — search-based org expansion → JSONL log for human review.
- `embed`          — embed DuckDB rows into Qdrant via RCP.
- `search`         — semantic retrieval (vector + rerank).
- `query`          — read-only SQL over the DuckDB dump.
- `status`         — counts + paths summary.
- `serve`          — run the FastAPI app on a chosen port.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from src.index.huggingface.config import load_config
from src.index.huggingface.ingest.datasets_ingest import ingest_datasets
from src.index.huggingface.ingest.discover_orgs import discover_orgs
from src.index.huggingface.ingest.hf_client import HFClient
from src.index.huggingface.ingest.models_ingest import ingest_models
from src.index.huggingface.ingest.orgs_ingest import ingest_orgs
from src.index.huggingface.ingest.scope import resolve_scope
from src.index.huggingface.ingest.spaces_ingest import ingest_spaces
from src.index.huggingface.models import ALL_EMBEDDABLE_TYPES, ALL_ENTITY_TYPES
from src.index.huggingface.retrieval.sql import run_adhoc, run_predefined
from src.index.huggingface.storage.duckdb_store import DuckDBStore
from src.index.huggingface.vector.qdrant_store import COLLECTION_FOR_TABLE, QdrantStore

LOGGER = logging.getLogger(__name__)

ENTITY_INGESTERS = {
    "models": ingest_models,
    "datasets": ingest_datasets,
    "spaces": ingest_spaces,
    "orgs": ingest_orgs,
}

# Embed accepts everything ingest accepts (orgs included).
EMBEDDABLE_TABLES = set(ALL_EMBEDDABLE_TYPES)


def _split_entities(raw: str, *, valid: set[str] | None = None) -> list[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    allowed = valid if valid is not None else set(ENTITY_INGESTERS)
    unknown = [p for p in parts if p not in allowed]
    if unknown:
        message = f"Unknown entity types: {unknown}. Known: {sorted(allowed)}"
        raise SystemExit(message)
    return parts


def _emit_json(value: object) -> None:
    json.dump(value, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config()
    scope = resolve_scope(args.scope, config)
    entities = _split_entities(args.types)
    client = HFClient(config)
    store = DuckDBStore.open()
    summary: dict[str, int] = {}
    for entity in entities:
        ingester = ENTITY_INGESTERS[entity]
        summary[entity] = ingester(
            config=config,
            client=client,
            store=store,
            scope=scope,
            limit=args.limit,
        )
    _emit_json({"scope": args.scope, "ingested": summary})
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    config = load_config()
    client = HFClient(config)
    candidates = discover_orgs(
        config=config,
        client=client,
        scope_name=args.scope,
        per_term_limit=args.per_term_limit,
    )
    _emit_json(
        {
            "scope": args.scope,
            "candidates": len(candidates),
            "log_path": str(config.paths.logs_dir / "discover_orgs.jsonl"),
            "new_namespaces": [c["namespace"] for c in candidates if not c["in_seed"]],
        },
    )
    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    from src.index.huggingface.embed.pipeline import embed_entities

    entities = _split_entities(args.types, valid=EMBEDDABLE_TABLES)
    config = load_config()
    config.require_rcp()
    store = DuckDBStore.open()
    summary = embed_entities(
        config=config,
        store=store,
        entity_tables=entities,
        limit=args.limit,
    )
    _emit_json({"embedded": summary})
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    from src.index.huggingface.retrieval.semantic import (
        semantic_search,
        semantic_search_with_facets,
    )

    config = load_config()
    config.require_rcp()
    filter_payload = _parse_filter(args.filter or [])
    facet_keys: tuple[str, ...] = tuple(
        k.strip() for k in (args.facets or "").split(",") if k.strip()
    )
    if facet_keys:
        hits, facets = semantic_search_with_facets(
            config=config,
            query=args.query,
            entity_type=args.type,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            filter_payload=filter_payload,
            facet_keys=facet_keys,
            facet_top_n=args.facet_top_n,
        )
        _emit_json({"hits": hits, "facets": facets})
    else:
        hits = semantic_search(
            config=config,
            query=args.query,
            entity_type=args.type,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            filter_payload=filter_payload,
        )
        _emit_json(hits)
    return 0


def _parse_filter(raw: list[str]) -> dict[str, object] | None:
    """Parse `key=value` flags into a Qdrant payload filter dict.

    Repeated keys collapse to a list (Qdrant's `MatchAny`). Values that look
    like ints are coerced; everything else is kept as a string.
    """
    if not raw:
        return None
    parsed: dict[str, object] = {}
    for item in raw:
        if "=" not in item:
            message = f"--filter must be key=value, got {item!r}"
            raise SystemExit(message)
        key, value = item.split("=", 1)
        coerced: object = int(value) if value.lstrip("-").isdigit() else value
        if key in parsed:
            existing = parsed[key]
            parsed[key] = [*existing, coerced] if isinstance(existing, list) else [existing, coerced]
        else:
            parsed[key] = coerced
    return parsed


def _cmd_query(args: argparse.Namespace) -> int:
    params: dict[str, object] = {}
    for raw in args.param or []:
        if "=" not in raw:
            message = f"--param must be key=value, got {raw!r}"
            raise SystemExit(message)
        key, value = raw.split("=", 1)
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


def _cmd_lineage(args: argparse.Namespace) -> int:
    from src.index.huggingface.retrieval.lineage import compute_lineage

    store = DuckDBStore.open()
    result = compute_lineage(args.repo_id, store=store, depth=args.depth)
    _emit_json(result)
    return 0


def _cmd_backfill_payloads(_: argparse.Namespace) -> int:
    from src.index.huggingface.embed.pipeline import backfill_model_base_payloads

    config = load_config()
    store = DuckDBStore.open()
    n = backfill_model_base_payloads(config=config, store=store)
    _emit_json({"chunks_updated": n})
    return 0


def _cmd_status(_: argparse.Namespace) -> int:
    config = load_config()
    store = DuckDBStore.open()
    counts = {
        "orgs": store.count("orgs"),
        "models": store.count("models"),
        "datasets": store.count("datasets"),
        "spaces": store.count("spaces"),
        "chunks": store.count("chunks"),
    }
    qdrant_counts: dict[str, int] = {}
    try:
        qdrant = QdrantStore(config)
        for collection in sorted(COLLECTION_FOR_TABLE.values()):
            qdrant_counts[collection] = qdrant.count(collection)
    except Exception as exc:  # noqa: BLE001
        qdrant_counts = {"error": str(exc)}
    _emit_json(
        {
            "duckdb_path": str(config.paths.duckdb_path),
            "cards_dir": str(config.paths.cards_dir),
            "logs_dir": str(config.paths.logs_dir),
            "duckdb_counts": counts,
            "qdrant_counts": qdrant_counts,
            "active_scope": config.scope.active,
            "rcp_configured": bool(config.rcp.token),
            "hf_token_configured": bool(config.huggingface.token),
        },
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "src.index.huggingface.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="src.index.huggingface",
        description="HuggingFace ingestion + RAG over EPFL/Switzerland",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Fetch metadata + READMEs into DuckDB")
    p_ingest.add_argument("--scope", choices=["epfl", "switzerland"], required=True)
    p_ingest.add_argument(
        "--types",
        default=",".join(ALL_ENTITY_TYPES),
        help="Comma-separated subset of: models, datasets, spaces, orgs",
    )
    p_ingest.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after this many repos per org (per-type).",
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_disc = sub.add_parser(
        "discover-orgs",
        help="Substring-search the Hub for unknown EPFL/Swiss namespaces",
    )
    p_disc.add_argument("--scope", choices=["epfl", "switzerland"], required=True)
    p_disc.add_argument("--per-term-limit", type=int, default=200)
    p_disc.set_defaults(func=_cmd_discover)

    p_e = sub.add_parser("embed", help="Embed DuckDB rows into Qdrant via RCP")
    p_e.add_argument(
        "--types",
        default=",".join(ALL_EMBEDDABLE_TYPES),
        help="Comma-separated subset of: models, datasets, spaces, orgs",
    )
    p_e.add_argument("--limit", type=int, default=None)
    p_e.set_defaults(func=_cmd_embed)

    p_s = sub.add_parser("search", help="Semantic retrieval (vector + rerank)")
    p_s.add_argument("query")
    p_s.add_argument(
        "--type",
        default="models",
        help="Entity type or table (models|datasets|spaces|orgs).",
    )
    p_s.add_argument("--top-k", type=int, default=10)
    p_s.add_argument("--candidate-k", type=int, default=50)
    p_s.add_argument(
        "--filter",
        action="append",
        help="Qdrant payload filter, key=value (repeatable). "
        "E.g. --filter namespace_kind=user --filter scope=switzerland",
    )
    p_s.add_argument(
        "--facets",
        default="",
        help="Comma-separated payload keys to aggregate alongside hits. "
        "E.g. --facets license,pipeline_tag,author. "
        "Counts entities (deduped on repo_id) over the candidate pool.",
    )
    p_s.add_argument("--facet-top-n", type=int, default=10)
    p_s.set_defaults(func=_cmd_search)

    p_q = sub.add_parser("query", help="Read-only SQL over the DuckDB dump")
    p_q.add_argument("sql", nargs="?", help="Ad-hoc SELECT/WITH (omit if --predefined)")
    p_q.add_argument("--predefined", help="Run a predefined named query")
    p_q.add_argument(
        "--param",
        action="append",
        help="key=value for predefined queries (repeatable)",
    )
    p_q.set_defaults(func=_cmd_query)

    p_st = sub.add_parser("status", help="Show counts + paths")
    p_st.set_defaults(func=_cmd_status)

    p_bf = sub.add_parser(
        "backfill-payloads",
        help="One-shot: push base_model payload to existing Qdrant points without re-embedding",
    )
    p_bf.set_defaults(func=_cmd_backfill_payloads)

    p_l = sub.add_parser(
        "lineage",
        help="Walk the base_models DAG from a repo_id (ancestors + descendants).",
    )
    p_l.add_argument("repo_id", help="e.g. epfl-llm/meditron-7b")
    p_l.add_argument("--depth", type=int, default=3)
    p_l.set_defaults(func=_cmd_lineage)

    p_v = sub.add_parser("serve", help="Run the FastAPI app")
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
