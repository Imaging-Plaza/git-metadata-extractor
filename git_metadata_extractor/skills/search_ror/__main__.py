"""CLI entry point for the `search_ror` skill.

Wraps :class:`git_metadata_extractor.providers.ror_rag.RorRagProvider`. The same
provider powers the in-process LLM agent tool in
`git_metadata_extractor/agents/llm/agent_tools/ror_rag.py` — this skill only changes the
transport (stdio + JSON), not the underlying logic.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from git_metadata_extractor.providers.ror_rag import build_default_provider
from git_metadata_extractor.skills._runtime import SkillError, run_async_skill


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gme-search-ror",
        description="Semantic search over the ROR (Research Organization Registry) RAG index.",
    )
    parser.add_argument("query", help="Free-text query (institution / lab / university name).")
    parser.add_argument(
        "--scope",
        choices=("worldwide", "europe", "switzerland", "epfl_ethz"),
        default="worldwide",
        help="ROR scope to search (default: worldwide).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Maximum hits to return (default: 10).",
    )
    parser.add_argument(
        "--country",
        default=None,
        help="Optional ISO 3166-1 alpha-2 country filter (e.g. CH, FR).",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="Apply the cross-encoder reranker on top of vector recall.",
    )
    return parser


async def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
    provider = build_default_provider()
    if provider is None:
        raise SkillError(
            "ROR RAG provider unavailable (V2_ROR_RAG_ENABLED disabled or "
            "config load failed). Check INDEX_QDRANT_URL and RCP_TOKEN.",
            kind="provider_unavailable",
        )
    filters = {"country_code": args.country} if args.country else None
    return await provider.search(
        args.query,
        scope_mode=args.scope,
        top_k=args.top_k,
        filters=filters,
        rerank=args.rerank,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_async_skill(lambda: _run(args))


if __name__ == "__main__":
    sys.exit(main())
