"""Async RAG provider over the SWISSUbase Qdrant index.

Single collection: ``swissubase_entities``, holding studies, datasets, persons
and institutions distinguished by their ``entity_type`` payload field rather
than by separate collections — which is why this is a single-collection
provider despite covering four entity kinds. The search flow lives in
`_rag_index.SingleCollectionRagProvider`; this module is the index's data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from open_pulse_sources.index.swissubase.embed.pipeline import SWISSUBASE_COLLECTION

from git_metadata_extractor.providers._rag_index import (
    RagIndexSpec,
    SingleCollectionRagProvider,
    build_provider,
)

if TYPE_CHECKING:
    from open_pulse_sources.index.swissubase.config import SwissubaseIndexConfig


def _rerank_text(payload: dict[str, Any]) -> str:
    title = (
        payload.get("title")
        or payload.get("display_name")
        or payload.get("name")
        or ""
    )
    discipline = payload.get("main_discipline") or ""
    return f"{title}\n{discipline}".strip()


SPEC = RagIndexSpec(
    label="swissubase_rag",
    collection=SWISSUBASE_COLLECTION,
    config_module="open_pulse_sources.index.swissubase.config",
    env_var="V2_SWISSUBASE_RAG_ENABLED",
    allowed_filter_keys=frozenset(
        {
            "entity_type",
            "study_id",
            "dataset_id",
            "person_key",
            "institution_key",
            "ref",
            "main_discipline",
            "sub_discipline",
            "progress",
            "year_start",
            "year_end",
            "access_right",
        },
    ),
    thin_keys=(
        "entity_type",
        "entity_id",
        "study_id",
        "dataset_id",
        "person_key",
        "institution_key",
        "ref",
        "title",
        "name",
        "display_name",
        "main_discipline",
        "sub_discipline",
        "progress",
        "year_start",
        "year_end",
        "dataset_count",
        "access_right",
        "source_url",
    ),
    rerank_text=_rerank_text,
)


class SwissubaseRagProvider(SingleCollectionRagProvider):
    """Async wrapper around the SWISSUbase Qdrant index."""

    SPEC: ClassVar[RagIndexSpec] = SPEC


def build_default_provider(
    cfg: SwissubaseIndexConfig | None = None,
) -> SwissubaseRagProvider | None:
    return build_provider(SwissubaseRagProvider, cfg)  # type: ignore[return-value]


__all__ = ["SwissubaseRagProvider", "build_default_provider"]
