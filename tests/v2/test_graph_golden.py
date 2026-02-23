from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import app
from src.v2.testing.mock_generator import generate_dataset

pytestmark = pytest.mark.xfail(
    reason="Red-phase contract test until /v2/graph is implemented and mounted.",
    strict=True,
)
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


@pytest.mark.parametrize(
    ("params", "golden_name"),
    [
        ([], "full_graph"),
        ([("entity_type", "Person")], "filtered_by_type"),
        ([("source_url", "https://github.com/owner/repo")], "filtered_by_source"),
    ],
)
def test_graph_endpoint_matches_golden_contract(
    params: list[tuple[str, str]],
    golden_name: str,
    load_golden: Callable[[str, str], Any],
) -> None:
    app.state.v2_mock_graph_dataset = generate_dataset(seed=42)
    expected = load_golden("graph", golden_name)
    status_code, actual_payload = _get_json("/v2/graph", params=params)

    assert status_code == HTTP_OK
    assert actual_payload == expected
