"""Ingest HuggingFace models for the configured seed orgs.

For each org slug, list all models, fetch detailed `model_info(expand=...)`,
download the README, and upsert into the DuckDB `models` table. The README
is persisted under `cards/models/<repo_id>/README.md`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.index.huggingface.ingest._common import (
    author_from_repo_id,
    base_models_from_card_data,
    card_data_to_dict,
    info_to_dict,
    normalise_dt,
)
from src.index.huggingface.ingest.cards import maybe_snapshot_full_card, write_readme
from src.index.huggingface.models import MODEL_EXPAND_FIELDS

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig
    from src.index.huggingface.ingest.hf_client import HFClient
    from src.index.huggingface.ingest.scope import Scope
    from src.index.huggingface.storage.duckdb_store import HuggingFaceStore

LOGGER = logging.getLogger(__name__)


def ingest_single_model(
    *,
    repo_id: str,
    config: HuggingFaceIndexConfig,
    client: HFClient,
    store: HuggingFaceStore,
) -> bool:
    """Fetch + upsert a single model. Returns True when persisted, False on miss."""
    info = client.model_info(repo_id, expand=MODEL_EXPAND_FIELDS)
    if info is None:
        return False
    row = _model_row(repo_id, info)
    raw = info_to_dict(info)
    store.upsert_model(row, raw)

    readme = client.fetch_readme(repo_id, repo_type="model")
    if readme:
        write_readme(
            config,
            entity_type="models",
            repo_id=repo_id,
            readme=readme,
        )
    maybe_snapshot_full_card(
        config=config,
        client=client,
        entity_type="models",
        repo_id=repo_id,
        repo_type="model",
    )
    return True


def ingest_models(
    *,
    config: HuggingFaceIndexConfig,
    client: HFClient,
    store: HuggingFaceStore,
    scope: Scope,
    limit: int | None = None,
) -> int:
    """Return the number of models upserted across the scope."""
    upserted = 0
    skipped = 0
    for slug in scope.seeds:
        store.upsert_org(slug=slug, scope=scope.name, source="seed")
        listed = list(client.list_models(slug, limit=limit))
        LOGGER.info("models: %s → %d listed", slug, len(listed))
        for stub in listed:
            repo_id = getattr(stub, "id", None) or getattr(stub, "modelId", None)
            if not repo_id:
                continue
            stub_sha = getattr(stub, "sha", None)
            if stub_sha and store.repo_sha("models", repo_id) == stub_sha:
                # Already in DB at this revision — skip the heavy info+readme fetch.
                skipped += 1
                continue
            if ingest_single_model(
                repo_id=repo_id, config=config, client=client, store=store,
            ):
                upserted += 1
            if limit is not None and upserted >= limit * len(scope.seeds):
                return upserted
    LOGGER.info("models: ingest summary upserted=%d skipped_unchanged=%d", upserted, skipped)
    return upserted


def _model_row(repo_id: str, info: object) -> dict[str, object | None]:
    from src.index.huggingface.iri import (  # noqa: PLC0415
        arxiv_dois_from_tags,
        model_iri,
        namespace_iri,
    )

    card_data = card_data_to_dict(getattr(info, "card_data", None) or getattr(info, "cardData", None))
    tags = list(getattr(info, "tags", None) or [])
    bare_author = (
        getattr(info, "author", None) or author_from_repo_id(repo_id)
    )
    return {
        "repo_id": model_iri(repo_id),
        "author": namespace_iri(bare_author) if bare_author else None,
        # Derived citation surface — arXiv mints a DOI for every preprint
        # as `10.48550/arXiv.<id>`. Models that cite arxiv via tags get
        # `https://doi.org/...` URLs here, deduped.
        "arxiv_dois": arxiv_dois_from_tags(tags),
        "sha": getattr(info, "sha", None),
        "pipeline_tag": getattr(info, "pipeline_tag", None),
        "library_name": getattr(info, "library_name", None),
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
        "base_models": base_models_from_card_data(card_data),
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
