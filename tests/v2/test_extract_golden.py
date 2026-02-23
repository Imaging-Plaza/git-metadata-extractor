from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import app
from src.v2.providers.mock_github import MockGitHubProvider

pytestmark = pytest.mark.xfail(
    reason="Red-phase contract test until /v2/extract is implemented and mounted.",
    strict=True,
)

GITHUB_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "github"
HTTP_OK = 200


@pytest.fixture(scope="module")
def mock_github_provider() -> MockGitHubProvider:
    return MockGitHubProvider(fixture_root=GITHUB_FIXTURE_ROOT)


def _get_json(path: str, params: dict[str, str]) -> tuple[int, Any]:
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
    ("source_url", "golden_name"),
    [
        ("github.com/owner/repo", "repo_github_com_owner_repo"),
        ("github.com/username", "user_github_com_username"),
        ("github.com/orgname", "org_github_com_orgname"),
    ],
)
def test_extract_endpoint_matches_golden_contract(
    source_url: str,
    golden_name: str,
    load_golden: Callable[[str, str], Any],
    mock_github_provider: MockGitHubProvider,
) -> None:
    app.state.v2_github_provider = mock_github_provider
    expected = load_golden("extract", golden_name)
    status_code, actual_payload = _get_json(
        f"/v2/extract/{source_url}",
        params={"output_format": expected["output_format"]},
    )

    assert status_code == HTTP_OK
    assert actual_payload == expected
