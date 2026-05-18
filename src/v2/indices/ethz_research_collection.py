"""Async ingest helper for the ETH Research Collection index.

Called from `POST /v2/indices/ethz_research_collection/ingest`. Each UUID
is dispatched to :func:`fetch_and_persist_item`, which uses the existing
DSpace client to GET ``/core/items/{uuid}`` and persist the raw JSON under
``raw/items/<uuid>.json``. Downstream stages (text fetch, match extraction,
embedding) remain batch-oriented over the raw folder and are out of scope
for this route.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.v2.api_models import (
    EthzResearchCollectionIngestRequest,
    IndexIngestJobStatus,
    IndexSearchHit,
    IndexSearchRequest,
    IndexSearchResponse,
)

if TYPE_CHECKING:
    from src.v2.indices.jobs import IndexIngestJobStore

logger = logging.getLogger(__name__)

INDEX_NAME = "ethz_research_collection"


def get_or_create_ethz_research_collection_resources(app_state: Any) -> Any | None:
    """Lazy-init (config,) on ``app.state``. DSpaceClient is per-request."""

    cached = getattr(app_state, "v2_ethz_research_collection_resources", None)
    if cached is not None:
        return cached
    try:
        from src.index.ethz_research_collection.config import load_config  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional dependency
        logger.warning(
            "ethz_research_collection ingest: index module unavailable — %s", exc,
        )
        return None
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ethz_research_collection ingest: config load failed — %s", exc,
        )
        return None
    app_state.v2_ethz_research_collection_resources = (config,)
    return app_state.v2_ethz_research_collection_resources


async def _ingest_one_item(uuid: str, *, config: Any) -> dict[str, Any]:
    """Run the full per-item pipeline for one ETHZ Research Collection UUID.

    Stages chained: ``discover`` (fetch item JSON) → ``text-fetch`` (download
    plain-text bundle) → ``extract-matches`` (regex GitHub/HF URLs) →
    ``extract-relations`` (pull author/journal UUIDs) → ``fetch-related``
    (download each Person/Org by UUID). Stops at the first stage that
    returns ``"not_found"`` / ``"error"`` so we don't waste calls on missing
    items, but reports every stage outcome on the result dict.

    Never raises; per-stage failures land on the returned dict.
    """
    result: dict[str, Any] = {"uuid": uuid}

    try:
        from src.index.ethz_research_collection.discover import (  # noqa: PLC0415
            fetch_and_persist_item,
        )
        result["item"] = await fetch_and_persist_item(config, uuid=uuid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ethz_research_collection: discover %s failed — %s", uuid, exc)
        result["item"] = "error"
        result["error"] = str(exc)
        return result

    if result["item"] not in {"persisted", "already_present"}:
        return result

    try:
        from src.index.ethz_research_collection.text_fetch import (  # noqa: PLC0415
            fetch_text_single,
        )
        result["text"] = await fetch_text_single(config, uuid=uuid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ethz_research_collection: text %s failed — %s", uuid, exc)
        result["text"] = "error"
        result["text_error"] = str(exc)

    try:
        from src.index.ethz_research_collection.extract_matches import (  # noqa: PLC0415
            extract_matches_single,
        )
        matches = extract_matches_single(uuid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ethz_research_collection: matches %s failed — %s", uuid, exc)
        matches = None
        result["matches_error"] = str(exc)
    result["matches"] = {
        "found": bool(matches),
        "matched_urls": matches.matched_urls if matches else [],
        "counts_by_host": matches.counts_by_host if matches else {},
    }

    try:
        from src.index.ethz_research_collection.extract_relations import (  # noqa: PLC0415
            extract_relations_single,
        )
        relations = extract_relations_single(uuid)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ethz_research_collection: relations %s failed — %s", uuid, exc)
        relations = None
        result["relations_error"] = str(exc)

    person_uuids = relations.person_uuids if relations else []
    org_uuids = relations.org_uuids if relations else []
    result["relations"] = {
        "person_uuids": person_uuids,
        "org_uuids": org_uuids,
    }

    if person_uuids or org_uuids:
        try:
            from src.index.ethz_research_collection.fetch_related import (  # noqa: PLC0415
                fetch_related_single,
            )
            related_summary: dict[str, dict[str, int]] = {
                "persons": {},
                "organizations": {},
            }
            for related_uuid in person_uuids:
                outcome = await fetch_related_single(
                    config, uuid=related_uuid, kind="person",
                )
                related_summary["persons"][outcome] = (
                    related_summary["persons"].get(outcome, 0) + 1
                )
            for related_uuid in org_uuids:
                outcome = await fetch_related_single(
                    config, uuid=related_uuid, kind="org",
                )
                related_summary["organizations"][outcome] = (
                    related_summary["organizations"].get(outcome, 0) + 1
                )
            result["related"] = related_summary
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ethz_research_collection: fetch-related %s failed — %s",
                uuid, exc,
            )
            result["related_error"] = str(exc)

    return result


async def run_ethz_research_collection_ingest_job(
    *,
    payload: EthzResearchCollectionIngestRequest,
    app_state: Any,
    job_store: IndexIngestJobStore,
    job_id: str,
) -> None:
    """Background task: ingest each UUID and persist the outcome."""

    try:
        existing = job_store.get(job_id)
        if existing is None:
            return
        existing.status = IndexIngestJobStatus.RUNNING
        existing.started_at = datetime.now(timezone.utc)
        job_store.set(existing)

        resources = get_or_create_ethz_research_collection_resources(app_state)
        if resources is None:
            existing.status = IndexIngestJobStatus.FAILED
            existing.completed_at = datetime.now(timezone.utc)
            existing.error = (
                "ethz_research_collection index module unavailable on this deployment"
            )
            job_store.set(existing)
            return
        (config,) = resources

        items_results: list[dict[str, Any]] = []
        for uuid in payload.uuids:
            result = await _ingest_one_item(uuid, config=config)
            items_results.append(result)

        finished = job_store.get(job_id) or existing
        finished.status = IndexIngestJobStatus.COMPLETED
        finished.completed_at = datetime.now(timezone.utc)
        persisted = sum(1 for r in items_results if r.get("item") == "persisted")
        already_present = sum(
            1 for r in items_results if r.get("item") == "already_present"
        )
        not_found = sum(1 for r in items_results if r.get("item") == "not_found")
        errors = sum(1 for r in items_results if r.get("item") == "error")
        text_written = sum(1 for r in items_results if r.get("text") == "written")
        matches_found = sum(
            1 for r in items_results
            if isinstance(r.get("matches"), dict) and r["matches"].get("found")
        )
        finished.summary = {
            "requested": len(payload.uuids),
            "item_persisted": persisted,
            "item_already_present": already_present,
            "item_not_found": not_found,
            "item_errors": errors,
            "text_written": text_written,
            "matches_found": matches_found,
            "items": items_results,
        }
        job_store.set(finished)
    except Exception as exc:
        logger.exception("ethz_research_collection ingest job %s failed", job_id)
        record = job_store.get(job_id)
        if record is None:
            return
        record.status = IndexIngestJobStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = str(exc)
        job_store.set(record)


async def run_ethz_research_collection_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    """Run a hybrid query against the ETHZ Research Collection index.

    Maps the uniform request fields onto the index's own ``pipeline.query``:
    ``target`` → query target (default ``chunks``), ``filter_payload`` →
    ChromaDB-style ``where`` clause, ``top_k`` is used for both the candidate
    pool (`top_k * 5`) and the rerank cutoff (`top_n = top_k`). The mode is
    fixed to ``hybrid`` since the v2 API uniform shape doesn't expose a mode
    selector; clients needing other modes can call the standalone serve app.

    Related persons / organizations dicts (when present) land on
    ``response.extra``.
    """
    resources = get_or_create_ethz_research_collection_resources(app_state)
    if resources is None:
        return None
    (config,) = resources
    target = payload.target or "chunks"
    from src.index.ethz_research_collection.pipeline import query  # noqa: PLC0415
    candidate_k = payload.candidate_k or max(payload.top_k * 5, 50)
    result = await query(
        config,
        payload.query,
        target=target,
        where=payload.filter_payload,
        top_k=candidate_k,
        top_n=payload.top_k,
        mode="hybrid",
        with_authors=False,
        with_orgs=False,
    )
    hits = [
        IndexSearchHit(
            id=str(row.get("id") or row.get("uuid") or ""),
            payload=row,
            entity=None,
        )
        for row in (result.rows or [])
    ]
    extra: dict[str, Any] | None = None
    related_persons = getattr(result, "related_persons", None)
    related_orgs = getattr(result, "related_organizations", None)
    if related_persons or related_orgs:
        extra = {
            "related_persons": related_persons or {},
            "related_organizations": related_orgs or {},
        }
    return IndexSearchResponse(
        index_name="ethz_research_collection",
        target=result.target,
        query=payload.query,
        hits=hits,
        extra=extra,
    )


__all__ = [
    "INDEX_NAME",
    "get_or_create_ethz_research_collection_resources",
    "run_ethz_research_collection_ingest_job",
    "run_ethz_research_collection_search",
]
