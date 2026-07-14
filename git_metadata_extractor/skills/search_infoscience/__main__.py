"""CLI entry point for the `search_infoscience` skill.

Wraps :class:`git_metadata_extractor.providers.infoscience_rag.InfoscienceRagProvider`.
PoC scope: search only. The full agent_tool also exposes
fetch_chunks/fetch_records — those become separate skills if/when needed.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from git_metadata_extractor.providers.infoscience_rag import build_default_provider
from git_metadata_extractor.skills._runtime import SkillError, run_async_skill


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gme-search-infoscience",
        description="Semantic search over the EPFL Infoscience RAG index.",
    )
    parser.add_argument("query", help="Free-text query (paper title, author, lab, keywords).")
    parser.add_argument(
        "--collection",
        choices=("chunks", "articles", "persons", "organizations"),
        default="chunks",
        help="Infoscience collection (default: chunks — paper body fragments).",
    )
    parser.add_argument("--top-k", type=int, default=10, help="Maximum hits (default: 10).")
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Repeatable filter. Allowlisted keys: has_github_match, has_hf_match, "
            "year, publication_type, language, lab_uuid, org_uuids, doi, orcid, "
            "ror_id, sciper_id, sciper_unit_id, author_uuids, subjects, keywords."
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
            "Infoscience RAG provider unavailable (V2_INFOSCIENCE_RAG_ENABLED disabled "
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
