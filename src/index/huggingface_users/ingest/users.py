"""Fetch + persist one HF user namespace card."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.huggingface_users.models import HFUserRecord

if TYPE_CHECKING:
    from src.index._huggingface_base.client import HFClient
    from src.index.huggingface_users.config import (
        HuggingFaceUsersIndexConfig,
    )
    from src.index.huggingface_users.storage.duckdb_store import (
        HuggingFaceUsersStore,
    )

LOGGER = logging.getLogger(__name__)


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _record_from_overview(slug: str, overview: Any) -> HFUserRecord:
    """Build an HFUserRecord from an HF namespace overview object.

    Accepts both dict and attribute-bearing objects (HF library returns
    a dataclass that exposes `.fullname` / `.num_models` / etc.; we
    don't depend on the exact class shape).
    """
    def _g(name: str) -> Any:
        if isinstance(overview, dict):
            return overview.get(name)
        return getattr(overview, name, None)

    return HFUserRecord(
        slug=slug,
        fullname=_g("fullname") or _g("full_name") or _g("name"),
        details=_g("details") or _g("description") or _g("bio"),
        avatar_url=_g("avatar_url") or _g("avatarUrl"),
        num_models=_coerce_int(_g("num_models") or _g("numModels")),
        num_datasets=_coerce_int(_g("num_datasets") or _g("numDatasets")),
        num_spaces=_coerce_int(_g("num_spaces") or _g("numSpaces")),
        num_followers=_coerce_int(_g("num_followers") or _g("numFollowers")),
        raw={"slug": slug},
    )


def ingest_single_user(
    *,
    config: HuggingFaceUsersIndexConfig,
    store: HuggingFaceUsersStore,
    client: HFClient,
    slug: str,
) -> str:
    """Fetch + upsert one user. Returns
    ``"ingested" | "skipped_404" | "skipped_org"``.

    ``HFClient.namespace_overview(slug)`` returns ``(kind, overview)``
    where ``kind`` is ``'user'`` or ``'org'``. We skip the org case so
    it lands in the sibling ``huggingface_organizations`` module
    instead.
    """
    del config
    result = client.namespace_overview(slug)
    if result is None:
        LOGGER.warning("ingest skip: namespace not found: %s", slug)
        return "skipped_404"
    kind, overview = result
    if kind == "org":
        LOGGER.info(
            "ingest skip: %s is an org namespace — belongs in huggingface_organizations",
            slug,
        )
        return "skipped_org"
    record = _record_from_overview(slug, overview)
    store.upsert_user(record)
    LOGGER.info(
        "ingested hf user %s (fullname=%s followers=%d)",
        slug, record.fullname or "-", record.num_followers,
    )
    return "ingested"
