"""FastAPI app exposing the OpenAlex index dual query surface."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.index.openalex.config import load_config
from src.index.openalex.retrieval.semantic import semantic_search
from src.index.openalex.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.openalex.storage.duckdb_store import OpenAlexStore
from src.index.openalex.vector.qdrant_store import QdrantStore

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="OpenAlex Index")


class SearchRequest(BaseModel):
    query: str
    entity_type: str = "works"
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
    qdrant_status = "ok"
    try:
        OpenAlexStore.open().count("works")
    except Exception as exc:  # noqa: BLE001
        duck_status = f"error: {exc}"
    try:
        QdrantStore(config).count("works")
    except Exception as exc:  # noqa: BLE001
        qdrant_status = f"error: {exc}"
    return {
        "duckdb": duck_status,
        "qdrant": qdrant_status,
        "rcp_configured": bool(config.rcp.token),
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
    raise HTTPException(
        status_code=400,
        detail="Pass `predefined` or `sql`",
    )


@app.get("/predefined")
def list_predefined() -> dict[str, list[str]]:
    return {"predefined": sorted(PREDEFINED_QUERIES)}


@app.get("/entity/{entity_type}/{openalex_id:path}")
def get_entity(entity_type: str, openalex_id: str) -> dict[str, Any]:
    if entity_type not in {
        "works",
        "authors",
        "institutions",
        "sources",
        "topics",
        "concepts",
    }:
        raise HTTPException(status_code=404, detail=f"unknown entity type: {entity_type}")
    store = OpenAlexStore.open()
    cur = store.connect().execute(
        f"SELECT * FROM {entity_type} WHERE openalex_id = ?",  # noqa: S608
        [openalex_id],
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row, strict=False))
