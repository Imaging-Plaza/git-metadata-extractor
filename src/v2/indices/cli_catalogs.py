"""`POST /v2/indices/{provider}/search` shims for the CLI-managed catalogs.

The 9 mature v2 catalogs each ship their own `src/v2/indices/<name>.py`
with `run_<name>_search` + `run_<name>_ingest_job` + a tuple cached on
`app_state.v2_<name>_resources`. The 5 CLI-only catalogs (ror,
infoscience, snsf, epfl_graph, communities) don't have that surface
because they're driven by `python -m src.index.<name> …` from cron, not
by per-record v2 ingest calls.

This module fills in the gap with minimal-viable `run_<name>_search`
adapters: they translate `IndexSearchRequest` → the catalog's
existing CLI-level query function, then wrap the result in
`IndexSearchResponse`. The extra catalog-specific filters (ROR
country, SNSF discipline_l1, …) are NOT exposed; callers that need
them keep using the CLI. The intent here is to give the open-pulse
Hub a uniform `/v2/indices/{provider}/search` for every catalog
whose Qdrant collection already exists.

`communities` is intentionally absent — it has no semantic search
infrastructure (DuckDB-only registry, no embeddings, no Qdrant
collection). Adding it would mean building a search path from
scratch, which belongs in a separate PR.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.v2.api_models import IndexSearchRequest, IndexSearchResponse
from src.v2.indices._search_common import hit_from_raw

if TYPE_CHECKING:
    from collections.abc import Iterable

LOGGER = logging.getLogger(__name__)


def _hits_from_records(records: Iterable[Any]) -> list[Any]:
    """Coerce per-catalog scored-record types into `IndexSearchHit`s.

    The four `query_rag` / `semantic_search` functions return slightly
    different result types — pydantic models, dataclasses, plain dicts.
    Normalise to a flat dict shape that `hit_from_raw` understands.
    """
    out: list[Any] = []
    for record in records:
        if isinstance(record, dict):
            raw = record
        elif hasattr(record, "model_dump"):
            raw = record.model_dump()
        elif hasattr(record, "__dataclass_fields__"):
            from dataclasses import asdict  # noqa: PLC0415

            raw = asdict(record)
        else:
            raw = {"id": str(record)}
        if "id" not in raw and "ror_id" in raw:
            raw["id"] = raw["ror_id"]
        out.append(hit_from_raw(raw))
    return out


# ---------------------------------------------------------------------------
# ROR — `query_rag(cfg, text, *, top_k, rerank_top_k, country)`
# ---------------------------------------------------------------------------


async def run_ror_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    del app_state
    try:
        from src.index.ror.config import load_config as load_ror_config  # noqa: PLC0415
        from src.index.ror.query import query_rag  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional dependency
        LOGGER.warning("ror search: module unavailable — %s", exc)
        return None
    try:
        cfg = load_ror_config()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("ror search: config init failed — %s", exc)
        return None
    records = await query_rag(cfg, payload.query, top_k=payload.top_k)
    return IndexSearchResponse(
        index_name="ror",
        target=payload.target,
        query=payload.query,
        hits=_hits_from_records(records),
    )


# ---------------------------------------------------------------------------
# SNSF — `query_rag(cfg, text, *, top_k, rerank_top_k, …filters)`
# ---------------------------------------------------------------------------


async def run_snsf_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    del app_state
    try:
        from src.index.snsf.config import load_config as load_snsf_config  # noqa: PLC0415
        from src.index.snsf.query import query_rag  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("snsf search: module unavailable — %s", exc)
        return None
    try:
        cfg = load_snsf_config()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("snsf search: config init failed — %s", exc)
        return None
    records = await query_rag(cfg, payload.query, top_k=payload.top_k)
    return IndexSearchResponse(
        index_name="snsf",
        target=payload.target,
        query=payload.query,
        hits=_hits_from_records(records),
    )


# ---------------------------------------------------------------------------
# Infoscience — `pipeline.query(cfg, text, *, target, top_k, top_n, mode)`
# ---------------------------------------------------------------------------


async def run_infoscience_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    del app_state
    try:
        from src.index.infoscience.config import (  # noqa: PLC0415
            load_config as load_infoscience_config,
        )
        from src.index.infoscience.pipeline import query as infoscience_query  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("infoscience search: module unavailable — %s", exc)
        return None
    try:
        cfg = load_infoscience_config()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("infoscience search: config init failed — %s", exc)
        return None
    target = payload.target or "chunks"
    try:
        result = await infoscience_query(
            cfg, payload.query, target=target, top_n=payload.top_k,
        )
    except ValueError as exc:
        # Bad `target`, missing collection — surface as a hits=[] result.
        LOGGER.warning("infoscience search: %s", exc)
        return IndexSearchResponse(
            index_name="infoscience",
            target=target,
            query=payload.query,
            hits=[],
            extra={"error": str(exc)},
        )
    records: Iterable[Any]
    if hasattr(result, "results"):
        records = result.results
    elif hasattr(result, "hits"):
        records = result.hits
    elif isinstance(result, (list, tuple)):
        records = result
    else:
        records = [result]
    return IndexSearchResponse(
        index_name="infoscience",
        target=target,
        query=payload.query,
        hits=_hits_from_records(records),
    )


# ---------------------------------------------------------------------------
# EPFL Graph disciplines — sync `semantic_search(*, config, query, top_k, …)`
# ---------------------------------------------------------------------------


async def run_epfl_graph_search(
    payload: IndexSearchRequest, app_state: Any,
) -> IndexSearchResponse | None:
    del app_state
    try:
        from src.index.epfl_graph.config import (  # noqa: PLC0415
            load_config as load_epfl_graph_config,
        )
        from src.index.epfl_graph.retrieval.semantic import (  # noqa: PLC0415
            semantic_search,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("epfl_graph search: module unavailable — %s", exc)
        return None
    try:
        cfg = load_epfl_graph_config()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("epfl_graph search: config init failed — %s", exc)
        return None
    candidate_k = payload.candidate_k or max(payload.top_k * 5, 50)
    records = await asyncio.to_thread(
        semantic_search,
        config=cfg, query=payload.query,
        top_k=payload.top_k,
        candidate_k=candidate_k,
    )
    return IndexSearchResponse(
        index_name="epfl_graph",
        target=payload.target,
        query=payload.query,
        hits=_hits_from_records(records),
    )


__all__ = [
    "run_epfl_graph_search",
    "run_infoscience_search",
    "run_ror_search",
    "run_snsf_search",
]
