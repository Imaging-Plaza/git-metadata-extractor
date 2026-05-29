"""Fetch + persist one HF dataset card."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.index.huggingface_datasets.models import DatasetRecord

if TYPE_CHECKING:
    from src.index._huggingface_base.client import HFClient
    from src.index.huggingface_datasets.config import (
        HuggingFaceDatasetsIndexConfig,
    )
    from src.index.huggingface_datasets.storage.duckdb_store import (
        HuggingFaceDatasetsStore,
    )

LOGGER = logging.getLogger(__name__)

_DATASET_EXPAND: tuple[str, ...] = (
    "sha",
    "lastModified",
    "license",
    "downloads",
    "downloadsAllTime",
    "likes",
    "gated",
    "private",
    "createdAt",
    "tags",
    "cardData",
    "citation",
    "paperswithcode_id",
)

# Match DOIs in free-form BibTeX text. Conservative — only catches the
# `doi = {10.…}` and bare `10.…` forms.
_DOI_RE = re.compile(
    r"(?:doi\s*=\s*\{(10\.\d{4,9}/[^}\s]+)\}|(?<!\w)(10\.\d{4,9}/[^\s\\}]+))",
    re.IGNORECASE,
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


def _extract_citation_dois(citation_text: str | None) -> list[str]:
    """Pull DOIs out of a BibTeX block. Returns canonical
    `https://doi.org/<bare>` URLs."""
    if not isinstance(citation_text, str) or not citation_text.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for match in _DOI_RE.finditer(citation_text):
        bare = match.group(1) or match.group(2)
        if not bare:
            continue
        bare = bare.strip().rstrip(".,;").rstrip("/")
        if bare in seen:
            continue
        seen.add(bare)
        out.append(f"https://doi.org/{bare}")
    return out


def _paperswithcode_url(pwc_id: Any) -> str | None:
    if not isinstance(pwc_id, str) or not pwc_id.strip():
        return None
    return f"https://paperswithcode.com/dataset/{pwc_id.strip()}"


def _record_from_info(repo_id: str, info: Any) -> DatasetRecord:
    tags = list(getattr(info, "tags", None) or [])
    card_data = getattr(info, "card_data", None) or getattr(info, "cardData", None) or {}
    if hasattr(card_data, "to_dict"):
        card_data_dict = card_data.to_dict()
    elif isinstance(card_data, dict):
        card_data_dict = dict(card_data)
    else:
        card_data_dict = {}

    citation = (
        getattr(info, "citation", None)
        or (card_data_dict.get("citation") if isinstance(card_data_dict, dict) else None)
    )
    pwc_id = getattr(info, "paperswithcode_id", None) or (
        card_data_dict.get("paperswithcode_id") if isinstance(card_data_dict, dict) else None
    )

    dataset_info_raw = getattr(info, "dataset_info", None)
    if isinstance(dataset_info_raw, dict):
        dataset_info = dataset_info_raw
    elif hasattr(dataset_info_raw, "__dict__"):
        dataset_info = {k: v for k, v in vars(dataset_info_raw).items() if not k.startswith("_")}
    else:
        dataset_info = {}

    return DatasetRecord(
        repo_id=repo_id,
        author=getattr(info, "author", None),
        sha=getattr(info, "sha", None),
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
        dataset_info=dataset_info,
        citation_text=citation if isinstance(citation, str) else None,
        paperswithcode_url=_paperswithcode_url(pwc_id),
        citation_dois=_extract_citation_dois(citation),
        raw={"repo_id": repo_id, "sha": getattr(info, "sha", None)},
    )


def ingest_single_dataset(
    *,
    config: HuggingFaceDatasetsIndexConfig,
    store: HuggingFaceDatasetsStore,
    client: HFClient,
    repo_id: str,
) -> str:
    """Fetch + upsert one dataset. Returns ``"ingested" | "skipped_404"``."""
    del config
    info = client.dataset_info(repo_id, expand=_DATASET_EXPAND)
    if info is None:
        LOGGER.warning("ingest skip: dataset not found or unreachable: %s", repo_id)
        return "skipped_404"
    record = _record_from_info(repo_id, info)
    store.upsert_dataset(record)
    LOGGER.info(
        "ingested dataset %s (downloads=%d likes=%d)",
        repo_id,
        record.downloads,
        record.likes,
    )
    return "ingested"
