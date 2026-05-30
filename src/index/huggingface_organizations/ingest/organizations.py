"""Fetch + persist one HF organization namespace card."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.index.huggingface_organizations.models import HFOrgRecord

if TYPE_CHECKING:
    from src.index._huggingface_base.client import HFClient
    from src.index.huggingface_organizations.config import (
        HuggingFaceOrganizationsIndexConfig,
    )
    from src.index.huggingface_organizations.storage.duckdb_store import (
        HuggingFaceOrganizationsStore,
    )

LOGGER = logging.getLogger(__name__)


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _record_from_overview(slug: str, overview: Any) -> HFOrgRecord:
    """Build an HFOrgRecord. Same logic as the users module — HF
    returns identical-shape overviews for both kinds."""
    def _g(name: str) -> Any:
        if isinstance(overview, dict):
            return overview.get(name)
        return getattr(overview, name, None)

    return HFOrgRecord(
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


def ingest_single_organization(
    *,
    config: HuggingFaceOrganizationsIndexConfig,
    store: HuggingFaceOrganizationsStore,
    client: HFClient,
    slug: str,
) -> str:
    """Fetch + upsert one organization. Returns
    ``"ingested" | "skipped_404" | "skipped_user"``.

    Skips records where HF says the namespace is a user — those land
    in ``src.index.huggingface_users`` instead.
    """
    del config
    result = client.namespace_overview(slug)
    if result is None:
        LOGGER.warning("ingest skip: namespace not found: %s", slug)
        return "skipped_404"
    kind, overview = result
    if kind == "user":
        LOGGER.info(
            "ingest skip: %s is a user namespace — belongs in huggingface_users",
            slug,
        )
        return "skipped_user"
    record = _record_from_overview(slug, overview)
    store.upsert_organization(record)
    LOGGER.info(
        "ingested hf org %s (fullname=%s followers=%d num_models=%d)",
        slug,
        record.fullname or "-",
        record.num_followers,
        record.num_models,
    )
    return "ingested"
