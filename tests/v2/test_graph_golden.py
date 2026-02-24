from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import app
from src.v2.graph.store import GraphStore

HTTP_OK = 200


def _get_json(path: str, params: list[tuple[str, str]]) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, params=params)
        return response.status_code, response.json()

    return asyncio.run(_run())


def _seed_graph_store(db_path) -> None:
    store = GraphStore(str(db_path))
    entities = [
        ("schema:Person", "0000-0002-1825-0097", {"schema:name": "Graph Person"}),
        (
            "schema:SoftwareSourceCode",
            "owner/repo",
            {
                "schema:name": "Graph Repository",
                "source_url": "https://github.com/owner/repo",
            },
        ),
        (
            "org:Organization",
            "https://ror.org/02s376052",
            {"schema:name": "Graph Organization"},
        ),
        (
            "org:Membership",
            "0000-0002-1825-0097_https://ror.org/02s376052",
            {},
        ),
        (
            "pulse:Contribution",
            "0000-0002-1825-0097_owner/repo",
            {"source_url": "https://github.com/owner/repo"},
        ),
        (
            "schema:ScholarlyArticle",
            "10.5281/example0001",
            {"schema:name": "Graph Article"},
        ),
    ]
    for entity_type, entity_id, data in entities:
        store.insert_entity(
            entity_type=entity_type,
            entity_id=entity_id,
            data=data,
            identifiers={},
            id_source="golden",
        )

    run_id = store.create_run("https://github.com/owner/repo", "repository")
    store.complete_run(
        run_id,
        stats={
            "entity_ids": [
                "owner/repo",
                "0000-0002-1825-0097_owner/repo",
            ],
        },
    )


@pytest.mark.parametrize(
    ("params", "golden_name"),
    [
        ([("include_intermediates", "false")], "full_graph"),
        (
            [("entity_type", "Person"), ("include_intermediates", "false")],
            "filtered_by_type",
        ),
        (
            [
                ("source_url", "https://github.com/owner/repo"),
                ("include_intermediates", "false"),
            ],
            "filtered_by_source",
        ),
    ],
)
def test_graph_endpoint_matches_golden_contract(
    params: list[tuple[str, str]],
    golden_name: str,
    load_golden: Callable[[str, str], Any],
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / f"{golden_name}.db"
    _seed_graph_store(db_path)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(db_path))

    expected = load_golden("graph", golden_name)
    status_code, actual_payload = _get_json("/v2/graph", params=params)

    assert status_code == HTTP_OK
    assert actual_payload == expected
