from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from rdflib import Graph as RDFGraph

from src.v2.api import v2_router
from src.v2.graph.export import ENTITY_URI_PREFIX
from src.v2.graph.store import GraphStore
from src.v2.api_models import V2GraphResponse

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

    for index in range(5):
        timestamp = f"2026-02-24T10:00:0{index}Z"
        store.insert_intermediate(
            source_url=SOURCE_URL_PRIMARY,
            agent_name="person_agent",
            run_id=run_primary,
            data={"sequence": index},
            intermediate_id=f"person-agent-{index}",
            created_at=timestamp,
        )
        store.insert_intermediate(
            source_url=SOURCE_URL_PRIMARY,
            agent_name="repo_agent",
            run_id=run_primary,
            data={"sequence": index},
            intermediate_id=f"repo-agent-{index}",
            created_at=timestamp,
        )
    store.insert_intermediate(
        source_url=SOURCE_URL_SECONDARY,
        agent_name="repo_agent",
        run_id=run_secondary,
        data={"source": "secondary"},
        intermediate_id="repo-agent-secondary",
        created_at="2026-02-24T09:59:59Z",
    )


def _request_json(
    path: str,
    *,
    app: FastAPI | None = None,
    params: list[tuple[str, str]] | None = None,
) -> tuple[int, dict[str, Any]]:
    target_app = app or _build_test_app()

    async def _run() -> tuple[int, dict[str, Any]]:
        transport = ASGITransport(app=target_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, params=params)
        return response.status_code, response.json()

    return asyncio.run(_run())


def _get_json(
    *,
    params: list[tuple[str, str]] | None = None,
) -> tuple[int, dict[str, Any]]:
    return _request_json("/v2/graph", params=params)


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
    assert all(
        item.get("data", {}).get("source") != "secondary"
        for item in payload["intermediates"]
        if isinstance(item, dict) and isinstance(item.get("data"), dict)
    )


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


def test_graph_endpoint_source_filter_matches_extract_graph_write(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "api_graph_extract_write.db"
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))
    app = _build_test_app()

    extract_status, extract_payload = _request_json(
        "/v2/extract/github.com/octocat/Hello-World",
        app=app,
        params=[("output_format", "json"), ("include_intermediates", "false")],
    )
    assert extract_status == HTTP_OK

    included_entity_ids = {
        entity["id"]
        for entity in [
            extract_payload["output"]["root_entity"],
            *extract_payload["output"]["related_entities"],
        ]
        if isinstance(entity, dict) and isinstance(entity.get("id"), str)
    }
    assert included_entity_ids

    graph_status, graph_payload = _request_json(
        "/v2/graph",
        app=app,
        params=[
            ("source_url", str(extract_payload["source_url"])),
            ("include_intermediates", "false"),
        ],
    )

    assert graph_status == HTTP_OK
    assert _payload_entity_ids(graph_payload) == included_entity_ids
