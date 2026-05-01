"""Search-based org discovery — surfaces unknown namespaces for human review.

Substring-searches the Hub via `list_models(search=term)` /
`list_datasets(search=term)` for each term in
`config.discovery.search_terms[scope]`, groups hits by namespace, and
writes candidates to `<INDEX_DATA_DIR>/huggingface/logs/discover_orgs.jsonl`.

Never auto-promotes namespaces into the seed — the user reviews and edits
`config/index/huggingface.yaml` by hand. This is the human-in-the-loop
safeguard against false positives like the personal `huggingface.co/EPFL`
account.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig
    from src.index.huggingface.ingest.hf_client import HFClient
    from src.index.huggingface.models import DiscoveryCandidate

LOGGER = logging.getLogger(__name__)

DEFAULT_PER_TERM_LIMIT = 200
MAX_SAMPLES_PER_NAMESPACE = 5
LOG_FILENAME = "discover_orgs.jsonl"


def discover_orgs(
    *,
    config: HuggingFaceIndexConfig,
    client: HFClient,
    scope_name: str,
    per_term_limit: int = DEFAULT_PER_TERM_LIMIT,
) -> list[dict]:
    """Run discovery. Returns the candidate list and writes the JSONL log."""
    seed = set(config.seed_for(scope_name))
    terms = config.search_terms_for(scope_name)

    hits_by_ns: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"models": set(), "datasets": set(), "matched_terms": set()},
    )

    for term in terms:
        for kind, lister in (
            ("models", client.search_models),
            ("datasets", client.search_datasets),
        ):
            for stub in _iter_with_limit(lister(term, limit=per_term_limit), per_term_limit):
                repo_id = getattr(stub, "id", None)
                if not repo_id or "/" not in repo_id:
                    continue
                namespace, _ = repo_id.split("/", 1)
                bucket = hits_by_ns[namespace]
                bucket[kind].add(repo_id)
                bucket["matched_terms"].add(term)

    candidates: list[dict] = []
    for namespace, bucket in sorted(hits_by_ns.items()):
        sample_pool = sorted(bucket["models"]) + sorted(bucket["datasets"])
        candidates.append(
            {
                "namespace": namespace,
                "hits_models": len(bucket["models"]),
                "hits_datasets": len(bucket["datasets"]),
                "hits_total": len(bucket["models"]) + len(bucket["datasets"]),
                "sample_repo_ids": sample_pool[:MAX_SAMPLES_PER_NAMESPACE],
                "matched_terms": sorted(bucket["matched_terms"]),
                "in_seed": namespace in seed,
            },
        )

    log_path = config.paths.logs_dir / LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        for cand in candidates:
            fh.write(json.dumps(cand, ensure_ascii=False) + "\n")
    LOGGER.info("discover_orgs: wrote %d candidates → %s", len(candidates), log_path)
    return candidates


def _iter_with_limit(iterable: Iterable, limit: int) -> Iterable:
    """huggingface_hub's `limit=` is sometimes ignored on search; enforce it."""
    yielded = 0
    for item in iterable:
        if yielded >= limit:
            return
        yielded += 1
        yield item
