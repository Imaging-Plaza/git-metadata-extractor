"""FastAPI app exposing the HuggingFace index dual query surface."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.index.huggingface.config import load_config
from src.index.huggingface.retrieval.semantic import semantic_search
from src.index.huggingface.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.huggingface.storage.duckdb_store import ENTITY_TABLES, HuggingFaceStore
from src.index.huggingface.vector.qdrant_store import COLLECTION_FOR_TABLE, QdrantStore

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="HuggingFace Index")


class SearchRequest(BaseModel):
    query: str
    entity_type: str = "models"
    top_k: int = Field(default=10, ge=1, le=100)
    candidate_k: int = Field(default=50, ge=1, le=500)
    filter_payload: dict[str, Any] | None = None


class QueryRequest(BaseModel):
    sql: str | None = None
    predefined: str | None = None
    params: dict[str, Any] | None = None


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """Probe DuckDB + Qdrant. RCP probe deferred to first /search call."""
    config = load_config()
    duck_status = "ok"
    qdrant_status: dict[str, Any] = {}
    try:
        HuggingFaceStore.open().count("models")
    except Exception as exc:  # noqa: BLE001
        duck_status = f"error: {exc}"
    try:
        store = QdrantStore(config)
        for collection in COLLECTION_FOR_TABLE.values():
            qdrant_status[collection] = store.count(collection)
    except Exception as exc:  # noqa: BLE001
        qdrant_status = {"error": str(exc)}
    return {
        "duckdb": duck_status,
        "qdrant": qdrant_status,
        "rcp_configured": bool(config.rcp.token),
        "hf_token_configured": bool(config.huggingface.token),
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
        entity_type=req.entity_type,
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


@app.get("/entity/{entity_table}/{repo_id:path}")
def get_entity(entity_table: str, repo_id: str) -> dict[str, Any]:
    if entity_table not in ENTITY_TABLES:
        raise HTTPException(
            status_code=404,
            detail=f"unknown entity table: {entity_table}",
        )
    store = HuggingFaceStore.open()
    row = store.fetch_repo(entity_table, repo_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row
