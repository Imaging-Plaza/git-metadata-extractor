"""CLI entry point for the `search_openalex` skill.

Wraps :class:`src.v2.ingest.providers.openalex_rag.OpenAlexRagProvider`.
OpenAlex is a worldwide scholarly graph (works, authors, institutions,
sources, topics, concepts) — use this when an entity is not in
EPFL-scoped Infoscience.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from src.v2.ingest.providers.openalex_rag import build_default_provider
from src.v2.skills._runtime import SkillError, run_async_skill


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gme-search-openalex",
        description="Semantic search over the OpenAlex worldwide scholarly graph.",
    )
    parser.add_argument("query", help="Free-text query (paper title, author, institution, topic).")
    parser.add_argument(
        "--collection",
        choices=("works", "authors", "institutions", "sources", "topics", "concepts"),
        default="works",
        help="OpenAlex collection (default: works — papers).",
    )
    parser.add_argument("--top-k", type=int, default=10, help="Maximum hits (default: 10).")
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Repeatable filter. Allowlisted keys: year, publication_year, doi, "
            "entity_type, openalex_id, country_code, type, field_id, domain_id, "
            "level, primary_topic_id, primary_source_id, last_known_institution_id, "
            "orcid, ror."
        ),
    )
    parser.add_argument("--rerank", action="store_true", help="Cross-encoder rerank.")
    return parser


def _parse_filters(items: list[str]) -> dict[str, Any] | None:
    if not items:
        return None
    out: dict[str, Any] = {}
    for raw in items:
        if "=" not in raw:
            msg = f"--filter expected KEY=VALUE, got {raw!r}"
            raise SkillError(msg, kind="bad_argument")
        k, v = raw.split("=", 1)
        out[k.strip()] = v.strip()
    return out


async def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
    provider = build_default_provider()
    if provider is None:
        raise SkillError(
            "OpenAlex RAG provider unavailable (V2_OPENALEX_RAG_ENABLED disabled "
            "or config load failed). Check INDEX_QDRANT_URL and RCP_TOKEN.",
            kind="provider_unavailable",
        )
    return await provider.search(
        args.query,
        collection=args.collection,
        top_k=args.top_k,
        filters=_parse_filters(args.filter),
        rerank=args.rerank,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_async_skill(lambda: _run(args))


if __name__ == "__main__":
    sys.exit(main())
