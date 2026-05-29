"""FastAPI app exposing the Zenodo index dual query surface."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.zenodo_records.config import load_config
from src.index.zenodo_records.embed.pipeline import ZENODO_COLLECTION
from src.index.zenodo_records.retrieval.semantic import semantic_search
from src.index.zenodo_records.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.zenodo_records.storage.duckdb_store import ZenodoRecordsStore

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="Zenodo Index")


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
        ZenodoRecordsStore.open().count("records")
    except Exception as exc:  # noqa: BLE001
        duck_status = f"error: {exc}"
    try:
        QdrantStore(config).count(ZENODO_COLLECTION)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        qdrant_status = f"error: {exc}"
    return {
        "duckdb": duck_status,
        "qdrant": qdrant_status,
        "rcp_configured": bool(config.rcp.token),
        "zenodo_token_configured": bool(config.zenodo.token),
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


@app.get("/record/{zenodo_id}")
def get_record(zenodo_id: str) -> dict[str, Any]:
    store = ZenodoRecordsStore.open()
    record = store.fetch_record(zenodo_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return record
