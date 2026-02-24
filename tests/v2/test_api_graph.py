from __future__ import annotations

import asyncio
import json
import sqlite3
from collections import Counter
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from rdflib import Graph as RDFGraph

from src.v2.api import v2_router
from src.v2.graph.export import ENTITY_URI_PREFIX
from src.v2.graph.store import GraphStore
from src.v2.models import V2GraphResponse

HTTP_OK = 200
SOURCE_URL_PRIMARY = "https://github.com/owner/repo-a"
SOURCE_URL_SECONDARY = "https://github.com/owner/repo-b"
INTERMEDIATE_LIMIT = 3


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _raw_entity_id(value: str) -> str:
    if value.startswith(ENTITY_URI_PREFIX):
        return value[len(ENTITY_URI_PREFIX) :]
    return value


def _payload_entity_ids(payload: dict[str, Any]) -> set[str]:
    entity_ids: set[str] = set()
    for node in payload["graph_jsonld"]["@graph"]:
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id")
        if isinstance(node_id, str):
            entity_ids.add(_raw_entity_id(node_id))
    return entity_ids


def _seed_graph_store(db_path) -> None:
    store = GraphStore(str(db_path))
    for entity_type, entity_id, name in (
        ("person", "person-1", "Ada"),
        ("person", "person-2", "Grace"),
        ("organization", "org-1", "EPFL"),
        ("repository", "repo-1", "project-a"),
        ("repository", "repo-2", "project-b"),
    ):
        store.insert_entity(
            entity_type=entity_type,
            entity_id=entity_id,
            data={"schema:name": name},
            identifiers={},
            id_source="test",
        )

    store.insert_edge("person-1", "repo-1", "contributes_to")
    store.insert_edge("person-2", "repo-2", "contributes_to")
    store.insert_edge("org-1", "repo-1", "owns")

    run_primary = store.create_run(SOURCE_URL_PRIMARY, "repository")
    store.complete_run(run_primary, {"entity_ids": ["person-1", "repo-1", "org-1"]})
    run_secondary = store.create_run(SOURCE_URL_SECONDARY, "repository")
    store.complete_run(run_secondary, {"entity_ids": ["person-2", "repo-2"]})

    with sqlite3.connect(db_path) as connection:
        for index in range(5):
            connection.execute(
                """
                INSERT INTO intermediates (
                    id,
                    source_url,
                    agent_name,
                    run_id,
                    data,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, datetime('now', ?));
                """,
                (
                    f"person-agent-{index}",
                    SOURCE_URL_PRIMARY,
                    "person_agent",
                    run_primary,
                    json.dumps({"sequence": index}),
                    f"-{index} seconds",
                ),
            )
            connection.execute(
                """
                INSERT INTO intermediates (
                    id,
                    source_url,
                    agent_name,
                    run_id,
                    data,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, datetime('now', ?));
                """,
                (
                    f"repo-agent-{index}",
                    SOURCE_URL_PRIMARY,
                    "repo_agent",
                    run_primary,
                    json.dumps({"sequence": index}),
                    f"-{index} seconds",
                ),
            )
        connection.commit()


def _get_json(
    *,
    params: list[tuple[str, str]] | None = None,
) -> tuple[int, dict[str, Any]]:
    async def _run() -> tuple[int, dict[str, Any]]:
        transport = ASGITransport(app=_build_test_app())
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get("/v2/graph", params=params)
        return response.status_code, response.json()

    return asyncio.run(_run())


def test_graph_endpoint_returns_graph_response_with_entities(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "api_graph.db"
    _seed_graph_store(db_path)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))

    status_code, payload = _get_json()

    assert status_code == HTTP_OK
    assert len(payload["graph_jsonld"]["@graph"]) > 0
    assert V2GraphResponse.model_validate(payload)


def test_graph_endpoint_filters_by_source_url(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "api_graph_source.db"
    _seed_graph_store(db_path)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))

    status_code, payload = _get_json(params=[("source_url", SOURCE_URL_PRIMARY)])

    assert status_code == HTTP_OK
    assert _payload_entity_ids(payload) == {"person-1", "repo-1", "org-1"}


def test_graph_endpoint_filters_by_entity_type(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "api_graph_type.db"
    _seed_graph_store(db_path)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))

    status_code, payload = _get_json(params=[("entity_type", "Person")])

    assert status_code == HTTP_OK
    assert _payload_entity_ids(payload) == {"person-1", "person-2"}


def test_graph_endpoint_includes_intermediates_when_requested(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "api_graph_intermediates_on.db"
    _seed_graph_store(db_path)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))

    status_code, payload = _get_json(
        params=[
            ("source_url", SOURCE_URL_PRIMARY),
            ("include_intermediates", "true"),
        ],
    )

    assert status_code == HTTP_OK
    assert isinstance(payload.get("intermediates"), list)
    assert len(payload["intermediates"]) > 0


def test_graph_endpoint_excludes_intermediates_when_disabled(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "api_graph_intermediates_off.db"
    _seed_graph_store(db_path)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))

    status_code, payload = _get_json(
        params=[
            ("source_url", SOURCE_URL_PRIMARY),
            ("include_intermediates", "false"),
        ],
    )

    assert status_code == HTTP_OK
    assert "intermediates" not in payload


def test_graph_endpoint_respects_intermediate_limit_per_agent(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "api_graph_intermediates_limit.db"
    _seed_graph_store(db_path)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))

    status_code, payload = _get_json(
        params=[
            ("source_url", SOURCE_URL_PRIMARY),
            ("include_intermediates", "true"),
            ("intermediate_limit", "3"),
        ],
    )

    assert status_code == HTTP_OK
    counts_by_agent = Counter(
        item["agent_name"]
        for item in payload.get("intermediates", [])
        if isinstance(item, dict) and "agent_name" in item
    )
    assert counts_by_agent["person_agent"] == INTERMEDIATE_LIMIT
    assert counts_by_agent["repo_agent"] == INTERMEDIATE_LIMIT


def test_graph_endpoint_stats_match_graph_payload(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "api_graph_stats.db"
    _seed_graph_store(db_path)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))

    status_code, payload = _get_json()

    assert status_code == HTTP_OK
    assert payload["stats"]["entities_count"] == len(payload["graph_jsonld"]["@graph"])
    parsed_graph = RDFGraph()
    parsed_graph.parse(data=json.dumps(payload["graph_jsonld"]), format="json-ld")
    assert payload["stats"]["triples_count"] == len(parsed_graph)
