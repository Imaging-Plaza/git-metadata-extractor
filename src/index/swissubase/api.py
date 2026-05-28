"""FastAPI app exposing the SWISSUbase index dual query surface."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.swissubase.config import load_config
from src.index.swissubase.embed.pipeline import SWISSUBASE_COLLECTION
from src.index.swissubase.retrieval.semantic import semantic_search
from src.index.swissubase.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.swissubase.storage.duckdb_store import SwissubaseStore

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="SWISSUbase Index")


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    candidate_k: int = Field(default=50, ge=1, le=500)
    filter_payload: dict[str, Any] | None = None


class QueryRequest(BaseModel):
    sql: str | None = None
    predefined: str | None = None
    params: dict[str, Any] | None = None


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    config = load_config()
    duck_status = "ok"
    qdrant_status = "ok"
    try:
        SwissubaseStore.open().count("studies")
    except Exception as exc:  # noqa: BLE001
        duck_status = f"error: {exc}"
    try:
        QdrantStore(config).count(SWISSUBASE_COLLECTION)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        qdrant_status = f"error: {exc}"
    return {
        "duckdb": duck_status,
        "qdrant": qdrant_status,
        "rcp_configured": bool(config.rcp.token),
        "selenium_configured": bool(config.selenium.remote_url),
        "scope_default": config.scope.default,
    }


@app.post("/search")
def search(req: SearchRequest) -> list[dict[str, Any]]:
    config = load_config()
    try:
        config.require_rcp()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return semantic_search(
        config=config,
        query=req.query,
        top_k=req.top_k,
        candidate_k=req.candidate_k,
        filter_payload=req.filter_payload,
    )


@app.post("/query")
def query(req: QueryRequest) -> list[dict[str, Any]]:
    if req.predefined and req.sql:
        raise HTTPException(
            status_code=400,
            detail="Provide either `predefined` or `sql`, not both",
        )
    try:
        if req.predefined:
            return run_predefined(req.predefined, req.params)
        if req.sql:
            return run_adhoc(req.sql, req.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail="Pass `predefined` or `sql`")


@app.get("/predefined")
def list_predefined() -> dict[str, list[str]]:
    return {"predefined": sorted(PREDEFINED_QUERIES)}


@app.get("/study/{study_id}")
def get_study(study_id: str) -> dict[str, Any]:
    store = SwissubaseStore.open()
    record = store.fetch_study(study_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return record


@app.get("/dataset/{dataset_id}")
def get_dataset(dataset_id: str) -> dict[str, Any]:
    store = SwissubaseStore.open()
    record = store.fetch_dataset(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return record
