"""Fetch + persist one HF Space card."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.index.huggingface_spaces.models import SpaceRecord

if TYPE_CHECKING:
    from src.index._huggingface_base.client import HFClient
    from src.index.huggingface_spaces.config import (
        HuggingFaceSpacesIndexConfig,
    )
    from src.index.huggingface_spaces.storage.duckdb_store import (
        HuggingFaceSpacesStore,
    )

LOGGER = logging.getLogger(__name__)

_SPACE_EXPAND: tuple[str, ...] = (
    "sha",
    "lastModified",
    "sdk",
    "license",
    "likes",
    "createdAt",
    "tags",
    "cardData",
    "runtime",
)


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _runtime_fields(info: Any) -> tuple[str | None, str | None]:
    """Extract `(stage, hardware)` from the HF runtime block.

    SpaceInfo exposes ``runtime`` (a SpaceRuntime dataclass). We tolerate
    either a dict or an attribute-bearing object.
    """
    runtime = getattr(info, "runtime", None)
    if runtime is None:
        return None, None
    if isinstance(runtime, dict):
        stage = runtime.get("stage")
        hardware = runtime.get("hardware") or runtime.get("requestedHardware")
    else:
        stage = getattr(runtime, "stage", None)
        hardware = (
            getattr(runtime, "hardware", None)
            or getattr(runtime, "requested_hardware", None)
        )
    return (
        str(stage) if stage is not None else None,
        str(hardware) if hardware is not None else None,
    )


def _record_from_info(repo_id: str, info: Any) -> SpaceRecord:
    tags = list(getattr(info, "tags", None) or [])
    card_data = getattr(info, "card_data", None) or getattr(info, "cardData", None) or {}
    if hasattr(card_data, "to_dict"):
        card_data_dict = card_data.to_dict()
    elif isinstance(card_data, dict):
        card_data_dict = dict(card_data)
    else:
        card_data_dict = {}

    stage, hardware = _runtime_fields(info)

    return SpaceRecord(
        repo_id=repo_id,
        author=getattr(info, "author", None),
        sha=getattr(info, "sha", None),
        sdk=getattr(info, "sdk", None),
        runtime_stage=stage,
        hardware=hardware,
        license=card_data_dict.get("license") if isinstance(card_data_dict, dict) else None,
        likes=int(getattr(info, "likes", None) or 0),
        created_at=_to_datetime(
            getattr(info, "created_at", None) or getattr(info, "createdAt", None),
        ),
        last_modified=_to_datetime(
            getattr(info, "last_modified", None) or getattr(info, "lastModified", None),
        ),
        tags=tags,
        card_data=card_data_dict if isinstance(card_data_dict, dict) else {},
        raw={"repo_id": repo_id, "sha": getattr(info, "sha", None)},
    )


def ingest_single_space(
    *,
    config: HuggingFaceSpacesIndexConfig,
    store: HuggingFaceSpacesStore,
    client: HFClient,
    repo_id: str,
) -> str:
    """Fetch + upsert one Space. Returns ``"ingested" | "skipped_404"``."""
    del config
    info = client.space_info(repo_id, expand=_SPACE_EXPAND)
    if info is None:
        LOGGER.warning("ingest skip: space not found or unreachable: %s", repo_id)
        return "skipped_404"
    record = _record_from_info(repo_id, info)
    store.upsert_space(record)
    LOGGER.info(
        "ingested space %s (sdk=%s likes=%d)",
        repo_id, record.sdk or "-", record.likes,
    )
    return "ingested"
