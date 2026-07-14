"""CLI entry point for the `search_orcid` skill.

Wraps :class:`git_metadata_extractor.providers.orcid_rag.OrcidRagProvider`. The
same provider powers the in-process LLM agent tool in
`git_metadata_extractor/agents/llm/agent_tools/orcid_rag.py`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from git_metadata_extractor.providers.orcid_rag import build_default_provider
from git_metadata_extractor.skills._runtime import SkillError, run_async_skill


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gme-search-orcid",
        description="Semantic search over the ORCID RAG index (persons, employments, educations).",
    )
    parser.add_argument("query", help="Free-text query (person name, lab, institution, biography snippet).")
    parser.add_argument(
        "--entity-type",
        choices=("persons", "employments", "educations"),
        default="persons",
        help="ORCID entity type to search (default: persons).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Maximum hits to return (default: 10).",
    )
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Repeatable filter; only allowlisted keys are honoured "
            "(orcid_id, in_scope, discovered_via, org_ror, organization, "
            "department, role)."
        ),
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="Apply the cross-encoder reranker on top of vector recall.",
    )
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
            "ORCID RAG provider unavailable (V2_ORCID_RAG_ENABLED disabled or "
            "config load failed). Check INDEX_QDRANT_URL and RCP_TOKEN.",
            kind="provider_unavailable",
        )
    return await provider.search(
        args.query,
        entity_type=args.entity_type,
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
