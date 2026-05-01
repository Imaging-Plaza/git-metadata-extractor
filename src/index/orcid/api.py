"""FastAPI app exposing the ORCID index dual query surface."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.index.orcid.config import load_config
from src.index.orcid.retrieval.semantic import semantic_search
from src.index.orcid.retrieval.sql import (
    PREDEFINED_QUERIES,
    run_adhoc,
    run_predefined,
)
from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore
from src.index.orcid.vector.qdrant_store import OrcidQdrantStore

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="ORCID Index")


class SearchRequest(BaseModel):
    query: str
    entity_type: str = "persons"
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
        OrcidDuckDBStore.open().count("persons")
    except Exception as exc:  # noqa: BLE001
        duck_status = f"error: {exc}"
    try:
        OrcidQdrantStore(config).count("persons")
    except Exception as exc:  # noqa: BLE001
        qdrant_status = f"error: {exc}"
    return {
        "scope": config.paths.scope,
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
    raise HTTPException(status_code=400, detail="Pass `predefined` or `sql`")


@app.get("/predefined")
def list_predefined() -> dict[str, list[str]]:
    return {"predefined": sorted(PREDEFINED_QUERIES)}


@app.get("/person/{orcid_id}")
def get_person(orcid_id: str) -> dict[str, Any]:
    store = OrcidDuckDBStore.open()
    person = store.fetch_person(orcid_id)
    if person is None:
        raise HTTPException(status_code=404, detail="not found")
    person["employments"] = store.list_employments(orcid_id)
    return person
