"""Ingest HuggingFace datasets for the configured seed orgs."""

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
from src.index.huggingface.models import DATASET_EXPAND_FIELDS

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig
    from src.index.huggingface.ingest.hf_client import HFClient
    from src.index.huggingface.ingest.scope import Scope
    from src.index.huggingface.storage.duckdb_store import DuckDBStore

LOGGER = logging.getLogger(__name__)


def ingest_single_dataset(
    *,
    repo_id: str,
    config: HuggingFaceIndexConfig,
    client: HFClient,
    store: DuckDBStore,
) -> bool:
    """Fetch + upsert a single dataset. Returns True when persisted, False on miss."""
    info = client.dataset_info(repo_id, expand=DATASET_EXPAND_FIELDS)
    if info is None:
        return False
    row = _dataset_row(repo_id, info)
    raw = info_to_dict(info)
    store.upsert_dataset(row, raw)

    readme = client.fetch_readme(repo_id, repo_type="dataset")
    if readme:
        write_readme(
            config,
            entity_type="datasets",
            repo_id=repo_id,
            readme=readme,
        )
    maybe_snapshot_full_card(
        config=config,
        client=client,
        entity_type="datasets",
        repo_id=repo_id,
        repo_type="dataset",
    )
    return True


def ingest_datasets(
    *,
    config: HuggingFaceIndexConfig,
    client: HFClient,
    store: DuckDBStore,
    scope: Scope,
    limit: int | None = None,
) -> int:
    upserted = 0
    skipped = 0
    for slug in scope.seeds:
        store.upsert_org(slug=slug, scope=scope.name, source="seed")
        listed = list(client.list_datasets(slug, limit=limit))
        LOGGER.info("datasets: %s → %d listed", slug, len(listed))
        for stub in listed:
            repo_id = getattr(stub, "id", None) or getattr(stub, "datasetId", None)
            if not repo_id:
                continue
            stub_sha = getattr(stub, "sha", None)
            if stub_sha and store.repo_sha("datasets", repo_id) == stub_sha:
                skipped += 1
                continue
            if ingest_single_dataset(
                repo_id=repo_id, config=config, client=client, store=store,
            ):
                upserted += 1
            if limit is not None and upserted >= limit * len(scope.seeds):
                return upserted
    LOGGER.info("datasets: ingest summary upserted=%d skipped_unchanged=%d", upserted, skipped)
    return upserted


def _dataset_row(repo_id: str, info: object) -> dict[str, object | None]:
    from src.index.huggingface.iri import (  # noqa: PLC0415
        dataset_iri,
        dois_from_bibtex,
        namespace_iri,
        paperswithcode_url,
    )

    card_data = card_data_to_dict(getattr(info, "card_data", None) or getattr(info, "cardData", None))
    tags = list(getattr(info, "tags", None) or [])
    dataset_info_payload = getattr(info, "dataset_info", None) or getattr(info, "datasetInfo", None)
    bare_author = (
        getattr(info, "author", None) or author_from_repo_id(repo_id)
    )
    citation_text = getattr(info, "citation", None)
    if not isinstance(citation_text, str):
        citation_text = None
    pwc_id = getattr(info, "paperswithcode_id", None) or getattr(
        info, "paperswithcodeId", None,
    )
    return {
        "repo_id": dataset_iri(repo_id),
        "author": namespace_iri(bare_author) if bare_author else None,
        "citation_text": citation_text,
        "paperswithcode_url": paperswithcode_url(pwc_id),
        "citation_dois": dois_from_bibtex(citation_text),
        "sha": getattr(info, "sha", None),
        "license": _license_from(card_data, getattr(info, "license", None)),
        "downloads": _coerce_int(getattr(info, "downloads", None)),
        "downloads_all_time": _coerce_int(
            getattr(info, "downloads_all_time", None)
            or getattr(info, "downloadsAllTime", None),
        ),
        "likes": _coerce_int(getattr(info, "likes", None)),
        "gated": _coerce_bool(getattr(info, "gated", None)),
        "private": _coerce_bool(getattr(info, "private", None)),
        "created_at": normalise_dt(getattr(info, "created_at", None) or getattr(info, "createdAt", None)),
        "last_modified": normalise_dt(getattr(info, "last_modified", None) or getattr(info, "lastModified", None)),
        "tags": tags,
        "card_data": card_data,
        "dataset_info": _coerce_dict(dataset_info_payload),
    }


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


def _coerce_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _coerce_dict(value: object) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    return None
