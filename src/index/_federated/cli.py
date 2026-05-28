"""CLI: `python -m src.index._federated <subcommand>`.

Subcommands:
- `search QUERY`   — federated semantic search across registered indices
- `entity ID`      — cross-index entity lookup (slug, ORCID, ROR, DOI, URL, …)
- `indices`        — list registered adapters and their entity types
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from src.index._federated.entity import cross_index_lookup
from src.index._federated.registry import REGISTRY, load_adapters
from src.index._federated.search import federated_search

LOGGER = logging.getLogger(__name__)


def _emit_json(value: object) -> None:
    json.dump(value, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _parse_kv(raw: list[str] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    out: dict[str, Any] = {}
    for item in raw:
        if "=" not in item:
            message = f"--filter must be key=value, got {item!r}"
            raise SystemExit(message)
        key, value = item.split("=", 1)
        coerced: Any = int(value) if value.lstrip("-").isdigit() else value
        if key in out:
            existing = out[key]
            out[key] = [*existing, coerced] if isinstance(existing, list) else [existing, coerced]
        else:
            out[key] = coerced
    return out


def _parse_indices(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def _cmd_search(args: argparse.Namespace) -> int:
    result = federated_search(
        args.query,
        indices=_parse_indices(args.indices),
        entity_type=args.entity_type,
        top_k_per_index=args.top_k_per_index,
        top_k_overall=args.top_k,
        filters=_parse_kv(args.filter),
        rerank=args.rerank,
    )
    _emit_json(result)
    return 0


def _cmd_entity(args: argparse.Namespace) -> int:
    result = cross_index_lookup(
        args.identifier,
        indices=_parse_indices(args.indices),
    )
    _emit_json(result)
    return 0


def _cmd_indices(_: argparse.Namespace) -> int:
    load_adapters()
    _emit_json({
        name: {"entity_types": adapter.entity_types}
        for name, adapter in sorted(REGISTRY.items())
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="src.index._federated",
        description="Federated search + cross-index entity lookup over the gme RAG indices",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_s = sub.add_parser("search", help="Federated semantic search across registered indices")
    p_s.add_argument("query")
    p_s.add_argument(
        "--indices",
        default="",
        help="Comma-separated subset of registered adapters (default: all). "
        "E.g. --indices huggingface,openalex",
    )
    p_s.add_argument(
        "--entity-type", default=None,
        help="Restrict to one entity type within each index (e.g. models, works, persons)",
    )
    p_s.add_argument("--top-k", type=int, default=20, help="overall hits returned")
    p_s.add_argument("--top-k-per-index", type=int, default=5, help="hits requested from each index")
    p_s.add_argument(
        "--filter", action="append",
        help="Payload filter key=value (repeatable). Forwarded to every adapter "
        "as-is; adapters ignore unknown keys.",
    )
    p_s.add_argument(
        "--rerank", action="store_true",
        help="Send the merged candidate pool through RCP's cross-encoder once "
        "for a globally-fair ordering. Costs one extra RCP call.",
    )
    p_s.set_defaults(func=_cmd_search)

    p_e = sub.add_parser("entity", help="Cross-index lookup for an identifier")
    p_e.add_argument("identifier", help="slug, URL, ORCID, ROR, DOI, UUID, …")
    p_e.add_argument("--indices", default="", help="Comma-separated subset of indices")
    p_e.set_defaults(func=_cmd_entity)

    p_i = sub.add_parser("indices", help="List registered adapters")
    p_i.set_defaults(func=_cmd_indices)
    return p


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
