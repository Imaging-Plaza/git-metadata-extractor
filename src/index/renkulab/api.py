"""FastAPI app exposing the RenkuLab index dual query surface."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.index.openalex.vector.qdrant_store import QdrantStore
from src.index.renkulab.config import load_config
from src.index.renkulab.embed.pipeline import COLLECTION_BY_ENTITY
from src.index.renkulab.retrieval.semantic import semantic_search
from src.index.renkulab.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.renkulab.storage.duckdb_store import RenkulabStore

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="RenkuLab Index")


class SearchRequest(BaseModel):
    query: str
    entity_types: list[str] | None = None
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
    qdrant_status: dict[str, Any] = {}
    try:
        RenkulabStore.open().count("projects")
    except Exception as exc:  # noqa: BLE001
        duck_status = f"error: {exc}"
    try:
        qd = QdrantStore(config)  # type: ignore[arg-type]
        for entity_type, collection in COLLECTION_BY_ENTITY.items():
            try:
                qdrant_status[collection] = qd.count(collection)
            except Exception as exc:  # noqa: BLE001
                qdrant_status[collection] = f"error: {exc}"
    except Exception as exc:  # noqa: BLE001
        qdrant_status = {"_error": str(exc)}
    return {
        "duckdb": duck_status,
        "qdrant": qdrant_status,
        "rcp_configured": bool(config.rcp.token),
        "renkulab_token_configured": bool(config.renkulab.token),
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
        entity_types=req.entity_types,
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


@app.get("/entity/{entity_type}/{entity_id}")
def get_entity(entity_type: str, entity_id: str) -> dict[str, Any]:
    store = RenkulabStore.open()
    try:
        record = store.fetch_entity(entity_type, entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return record
