"""Fetch + persist one HF Papers card."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.index.huggingface_papers.models import PaperAuthor, PaperRecord

if TYPE_CHECKING:
    from src.index.huggingface_papers.config import HuggingFacePapersIndexConfig
    from src.index.huggingface_papers.ingest.hf_papers_client import (
        HFPapersClient,
    )
    from src.index.huggingface_papers.storage.duckdb_store import (
        HuggingFacePapersStore,
    )

LOGGER = logging.getLogger(__name__)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_authors(payload: dict[str, Any]) -> list[PaperAuthor]:
    """The /api/papers/{id} payload nests authors under
    `paper.authors`. Each entry is `{name, hidden, user: {user, _id,
    affiliation}}` — we flatten the user fields onto our PaperAuthor."""
    paper_block = payload.get("paper") if isinstance(payload.get("paper"), dict) else payload
    raw_authors = paper_block.get("authors") if isinstance(paper_block, dict) else None
    if not isinstance(raw_authors, list):
        return []
    out: list[PaperAuthor] = []
    for entry in raw_authors:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        user_block = entry.get("user") if isinstance(entry.get("user"), dict) else {}
        out.append(
            PaperAuthor(
                name=name.strip(),
                hidden=bool(entry.get("hidden", False)),
                user_id=user_block.get("user") or user_block.get("_id"),
                affiliation=(
                    user_block.get("affiliation")
                    if isinstance(user_block.get("affiliation"), str) else None
                ),
            ),
        )
    return out


def _arxiv_doi(arxiv_id: str) -> str:
    return f"10.48550/arXiv.{arxiv_id}"


def _record_from_payload(arxiv_id: str, payload: dict[str, Any]) -> PaperRecord:
    """The HF Papers API wraps the paper under a `paper` key alongside
    HF-specific signals at the top level (`upvotes`, `numComments`,
    `publishedAt`). Tolerate both shapes (top-level or nested).
    """
    paper_block = payload.get("paper") if isinstance(payload.get("paper"), dict) else payload
    title = (paper_block.get("title") if isinstance(paper_block, dict) else None) or ""

    def _get(key: str) -> Any:
        # Prefer the nested paper block, fall back to the top-level
        # payload (older API shape).
        if isinstance(paper_block, dict) and key in paper_block:
            return paper_block[key]
        return payload.get(key)

    summary = _get("summary")
    published_at = _parse_iso(_get("publishedAt"))
    submitted_at = _parse_iso(_get("submittedOnDailyAt") or _get("submittedAt"))

    ai_keywords = _get("aiKeywords") or _get("ai_keywords") or []
    if isinstance(ai_keywords, list):
        ai_keywords = [k for k in ai_keywords if isinstance(k, str) and k]
    else:
        ai_keywords = []

    linked_models = _get("linkedModels") or _get("models") or []
    if not isinstance(linked_models, list):
        linked_models = []
    linked_datasets = _get("linkedDatasets") or _get("datasets") or []
    if not isinstance(linked_datasets, list):
        linked_datasets = []

    return PaperRecord(
        arxiv_id=arxiv_id,
        title=str(title).strip(),
        summary=summary.strip() if isinstance(summary, str) else None,
        doi=_arxiv_doi(arxiv_id),
        authors=_extract_authors(payload),
        published_at=published_at,
        submitted_at=submitted_at,
        upvotes=int(_get("upvotes") or 0),
        num_comments=int(_get("numComments") or _get("num_comments") or 0),
        is_author_participating=_get("isAuthorParticipating")
        if isinstance(_get("isAuthorParticipating"), bool) else None,
        ai_summary=_get("aiSummary") if isinstance(_get("aiSummary"), str) else None,
        ai_keywords=ai_keywords,
        thumbnail=_get("thumbnail") if isinstance(_get("thumbnail"), str) else None,
        linked_models=linked_models,
        linked_datasets=linked_datasets,
        raw=payload,
    )


def ingest_single_paper(
    *,
    config: HuggingFacePapersIndexConfig,
    store: HuggingFacePapersStore,
    client: HFPapersClient,
    arxiv_id: str,
) -> str:
    """Fetch + upsert one paper. Returns ``"ingested" | "skipped_404"``.

    The `arxiv_id` here must already be normalised (no version
    suffix, no URL prefix). Use
    `src.index.huggingface_papers.ingest.hf_papers_client.normalize_arxiv_id`
    on wire input before calling this.
    """
    del config  # required for symmetry with the repo-index path
    payload = client.get_paper(arxiv_id)
    if not isinstance(payload, dict):
        LOGGER.warning("ingest skip: paper not found or unreachable: %s", arxiv_id)
        return "skipped_404"
    record = _record_from_payload(arxiv_id, payload)
    if not record.title:
        # Defensive: HF should never return a title-less paper; if it
        # does, we don't have enough to embed.
        LOGGER.warning("ingest skip: paper has no title: %s", arxiv_id)
        return "skipped_404"
    store.upsert_paper(record)
    LOGGER.info(
        "ingested paper %s (upvotes=%d authors=%d)",
        arxiv_id,
        record.upvotes,
        len(record.authors),
    )
    return "ingested"
