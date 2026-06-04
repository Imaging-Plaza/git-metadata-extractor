"""Fetch + persist one HF model card."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.index.huggingface_models.models import ModelRecord
from src.v2.canonicalization.huggingface import huggingface_iri

if TYPE_CHECKING:
    from src.index._huggingface_base.client import HFClient
    from src.index.huggingface_models.config import (
        HuggingFaceModelsIndexConfig,
    )
    from src.index.huggingface_models.storage.duckdb_store import (
        HuggingFaceModelsStore,
    )

LOGGER = logging.getLogger(__name__)

# Columns we always ask HF to expand on model_info calls. Lets us
# populate downloads/likes/license/etc. in one round-trip.
_MODEL_EXPAND: tuple[str, ...] = (
    "sha",
    "lastModified",
    "pipeline_tag",
    "library_name",
    "downloads",
    "downloadsAllTime",
    "likes",
    "gated",
    "private",
    "createdAt",
    "tags",
    "cardData",
)

_ARXIV_TAG_RE = re.compile(r"^arxiv:(.+?)(?:v\d+)?$", re.IGNORECASE)


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _extract_arxiv_dois(tags: list[str]) -> list[str]:
    """Walk `tags` for `arxiv:<id>` entries; emit canonical DOI URLs."""
    out: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        match = _ARXIV_TAG_RE.match(tag.strip())
        if match is None:
            continue
        arxiv_id = match.group(1).strip()
        if arxiv_id:
            out.append(f"https://doi.org/10.48550/arXiv.{arxiv_id}")
    return out


def _record_from_info(repo_id: str, info: Any) -> ModelRecord:
    """Build a ModelRecord from an HF ModelInfo object."""
    tags = list(getattr(info, "tags", None) or [])
    card_data = getattr(info, "card_data", None) or getattr(info, "cardData", None) or {}
    # ``card_data`` is a ModelCardData object; cast to a plain dict.
    if hasattr(card_data, "to_dict"):
        card_data_dict = card_data.to_dict()
    elif isinstance(card_data, dict):
        card_data_dict = dict(card_data)
    else:
        card_data_dict = {}
    base_models_raw = card_data_dict.get("base_model") if isinstance(card_data_dict, dict) else None
    if isinstance(base_models_raw, str):
        base_models = [base_models_raw]
    elif isinstance(base_models_raw, list):
        base_models = [b for b in base_models_raw if isinstance(b, str) and b]
    else:
        base_models = []
    return ModelRecord(
        repo_id=huggingface_iri(repo_id, "model") or repo_id,
        author=getattr(info, "author", None),
        sha=getattr(info, "sha", None),
        pipeline_tag=getattr(info, "pipeline_tag", None),
        library_name=getattr(info, "library_name", None),
        license=card_data_dict.get("license") if isinstance(card_data_dict, dict) else None,
        downloads=int(getattr(info, "downloads", None) or 0),
        downloads_all_time=int(
            getattr(info, "downloads_all_time", None)
            or getattr(info, "downloadsAllTime", None)
            or 0,
        ),
        likes=int(getattr(info, "likes", None) or 0),
        gated=bool(getattr(info, "gated", None)) if getattr(info, "gated", None) is not None else None,
        private=bool(getattr(info, "private", None)) if getattr(info, "private", None) is not None else None,
        created_at=_to_datetime(
            getattr(info, "created_at", None) or getattr(info, "createdAt", None),
        ),
        last_modified=_to_datetime(
            getattr(info, "last_modified", None) or getattr(info, "lastModified", None),
        ),
        tags=tags,
        card_data=card_data_dict if isinstance(card_data_dict, dict) else {},
        base_models=base_models,
        arxiv_dois=_extract_arxiv_dois(tags),
        raw={
            # Keep the minimal-but-useful subset; full ModelInfo objects
            # don't serialise cleanly to JSON.
            "repo_id": repo_id,
            "sha": getattr(info, "sha", None),
            "library_name": getattr(info, "library_name", None),
            "pipeline_tag": getattr(info, "pipeline_tag", None),
        },
    )


def ingest_single_model(
    *,
    config: HuggingFaceModelsIndexConfig,
    store: HuggingFaceModelsStore,
    client: HFClient,
    repo_id: str,
) -> str:
    """Fetch + upsert one model. Returns ``"ingested" | "skipped_404"``."""
    del config  # required for symmetry with the other ingest paths
    info = client.model_info(repo_id, expand=_MODEL_EXPAND)
    if info is None:
        LOGGER.warning("ingest skip: model not found or unreachable: %s", repo_id)
        return "skipped_404"
    record = _record_from_info(repo_id, info)
    store.upsert_model(record)
    LOGGER.info(
        "ingested model %s (downloads=%d likes=%d library=%s)",
        repo_id,
        record.downloads,
        record.likes,
        record.library_name or "-",
    )
    return "ingested"
