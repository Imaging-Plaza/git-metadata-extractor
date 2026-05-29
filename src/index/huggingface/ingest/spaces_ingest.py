"""Ingest HuggingFace spaces for the configured seed orgs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.index.huggingface.ingest._common import (
    author_from_repo_id,
    card_data_to_dict,
    info_to_dict,
    normalise_dt,
)
from src.index.huggingface.ingest.cards import maybe_snapshot_full_card, write_readme
from src.index.huggingface.models import SPACE_EXPAND_FIELDS

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig
    from src.index.huggingface.ingest.hf_client import HFClient
    from src.index.huggingface.ingest.scope import Scope
    from src.index.huggingface.storage.duckdb_store import HuggingFaceStore

LOGGER = logging.getLogger(__name__)


def ingest_single_space(
    *,
    repo_id: str,
    config: HuggingFaceIndexConfig,
    client: HFClient,
    store: HuggingFaceStore,
) -> bool:
    """Fetch + upsert a single space. Returns True when persisted, False on miss."""
    info = client.space_info(repo_id, expand=SPACE_EXPAND_FIELDS)
    if info is None:
        return False
    row = _space_row(repo_id, info)
    raw = info_to_dict(info)
    store.upsert_space(row, raw)

    readme = client.fetch_readme(repo_id, repo_type="space")
    if readme:
        write_readme(
            config,
            entity_type="spaces",
            repo_id=repo_id,
            readme=readme,
        )
    maybe_snapshot_full_card(
        config=config,
        client=client,
        entity_type="spaces",
        repo_id=repo_id,
        repo_type="space",
    )
    return True


def ingest_spaces(
    *,
    config: HuggingFaceIndexConfig,
    client: HFClient,
    store: HuggingFaceStore,
    scope: Scope,
    limit: int | None = None,
) -> int:
    upserted = 0
    skipped = 0
    for slug in scope.seeds:
        store.upsert_org(slug=slug, scope=scope.name, source="seed")
        listed = list(client.list_spaces(slug, limit=limit))
        LOGGER.info("spaces: %s → %d listed", slug, len(listed))
        for stub in listed:
            repo_id = getattr(stub, "id", None) or getattr(stub, "spaceId", None)
            if not repo_id:
                continue
            stub_sha = getattr(stub, "sha", None)
            if stub_sha and store.repo_sha("spaces", repo_id) == stub_sha:
                skipped += 1
                continue
            if ingest_single_space(
                repo_id=repo_id, config=config, client=client, store=store,
            ):
                upserted += 1
            if limit is not None and upserted >= limit * len(scope.seeds):
                return upserted
    LOGGER.info("spaces: ingest summary upserted=%d skipped_unchanged=%d", upserted, skipped)
    return upserted


def _space_row(repo_id: str, info: object) -> dict[str, object | None]:
    from src.index.huggingface.iri import namespace_iri, space_iri  # noqa: PLC0415

    card_data = card_data_to_dict(getattr(info, "card_data", None) or getattr(info, "cardData", None))
    tags = list(getattr(info, "tags", None) or [])
    runtime = getattr(info, "runtime", None)
    bare_author = (
        getattr(info, "author", None) or author_from_repo_id(repo_id)
    )
    return {
        "repo_id": space_iri(repo_id),
        "author": namespace_iri(bare_author) if bare_author else None,
        "sha": getattr(info, "sha", None),
        "sdk": getattr(info, "sdk", None) or _from_card("sdk", card_data),
        "runtime_stage": _runtime_stage(runtime),
        "hardware": _runtime_hardware(runtime),
        "license": _license_from(card_data, getattr(info, "license", None)),
        "likes": _coerce_int(getattr(info, "likes", None)),
        "created_at": normalise_dt(getattr(info, "created_at", None) or getattr(info, "createdAt", None)),
        "last_modified": normalise_dt(getattr(info, "last_modified", None) or getattr(info, "lastModified", None)),
        "tags": tags,
        "card_data": card_data,
    }


def _from_card(key: str, card_data: dict | None) -> str | None:
    if not card_data:
        return None
    value = card_data.get(key)
    return value if isinstance(value, str) else None


def _runtime_stage(runtime: object) -> str | None:
    if runtime is None:
        return None
    if isinstance(runtime, dict):
        return runtime.get("stage") if isinstance(runtime.get("stage"), str) else None
    return getattr(runtime, "stage", None)


def _runtime_hardware(runtime: object) -> str | None:
    if runtime is None:
        return None
    if isinstance(runtime, dict):
        hardware = runtime.get("hardware")
        if isinstance(hardware, dict):
            return hardware.get("current") or hardware.get("requested")
        if isinstance(hardware, str):
            return hardware
        return None
    hardware = getattr(runtime, "hardware", None)
    if hardware is None:
        return None
    return getattr(hardware, "current", None) or getattr(hardware, "requested", None)


def _license_from(card_data: dict | None, fallback: object) -> str | None:
    if card_data and isinstance(card_data.get("license"), str):
        return card_data["license"]
    if isinstance(fallback, str):
        return fallback
    return None


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
