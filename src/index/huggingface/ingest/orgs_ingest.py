"""Ingest HuggingFace org/user overview metadata for the configured seed.

Probes each slug as an organisation first, falls back to the user endpoint
on 404, and persists `fullname`, `details`, repo counts, and the raw payload
to the `orgs` table. Idempotent: re-running upserts on `slug`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig
    from src.index.huggingface.ingest.hf_client import HFClient
    from src.index.huggingface.ingest.scope import Scope
    from src.index.huggingface.storage.duckdb_store import HuggingFaceStore

LOGGER = logging.getLogger(__name__)


def ingest_orgs(
    *,
    config: HuggingFaceIndexConfig,  # noqa: ARG001 - kept for ingester signature parity
    client: HFClient,
    store: HuggingFaceStore,
    scope: Scope,
    limit: int | None = None,  # noqa: ARG001 - same signature as repo ingesters
) -> int:
    """Return the number of namespaces upserted across the scope."""
    upserted = 0
    for slug in scope.seeds:
        result = client.namespace_overview(slug)
        if result is None:
            LOGGER.info("orgs: %s not found on the Hub (skipping)", slug)
            store.upsert_org(slug=slug, scope=scope.name, source="seed")
            continue
        kind, info = result
        store.upsert_org(
            slug=slug,
            scope=scope.name,
            source="seed",
            namespace_kind=kind,
            fullname=getattr(info, "fullname", None),
            details=getattr(info, "details", None),
            avatar_url=getattr(info, "avatar_url", None),
            num_models=_coerce_int(getattr(info, "num_models", None)),
            num_datasets=_coerce_int(getattr(info, "num_datasets", None)),
            num_spaces=_coerce_int(getattr(info, "num_spaces", None)),
            num_followers=_coerce_int(getattr(info, "num_followers", None)),
            raw=_overview_to_dict(info),
        )
        LOGGER.info("orgs: %s (%s) → %s", slug, kind, getattr(info, "fullname", None))
        upserted += 1
    return upserted


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _overview_to_dict(info: Any) -> dict[str, Any]:
    """Best-effort conversion of an Organization/User dataclass to a dict."""
    if hasattr(info, "__dataclass_fields__"):
        return {f: getattr(info, f, None) for f in info.__dataclass_fields__}
    if hasattr(info, "__dict__"):
        return dict(info.__dict__)
    return {}
