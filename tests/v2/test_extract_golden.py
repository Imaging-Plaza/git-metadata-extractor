from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import app
from src.v2.agents import ProviderSet
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_orcid import MockORCIDProvider
from src.v2.providers.mock_ror import MockRORProvider

GITHUB_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "providers" / "github"
HTTP_OK = 200


@pytest.fixture
def mock_provider_set() -> ProviderSet:
    return ProviderSet(
        github=MockGitHubProvider(fixture_root=GITHUB_FIXTURE_ROOT),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
        ror=MockRORProvider(),
    )


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


def _assert_subset(expected: Any, actual: Any) -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        for key, expected_value in expected.items():
            assert key in actual
            _assert_subset(expected_value, actual[key])
        return

    if isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) >= len(expected)
        for index, expected_item in enumerate(expected):
            _assert_subset(expected_item, actual[index])
        return

    assert actual == expected


@pytest.mark.parametrize(
    ("source_url", "golden_name"),
    [
        ("github.com/octocat/Hello-World", "repo_github_com_owner_repo"),
        ("github.com/octocat", "user_github_com_username"),
        ("github.com/orgs/github", "org_github_com_orgname"),
    ],
)
def test_extract_endpoint_matches_golden_contract(
    source_url: str,
    golden_name: str,
    load_golden: Callable[[str, str], Any],
    mock_provider_set: ProviderSet,
) -> None:
    app.state.v2_provider_set = mock_provider_set
    expected = load_golden("extract", golden_name)
    status_code, actual_payload = _get_json(
        f"/v2/extract/{source_url}",
        params={"output_format": expected["output_format"]},
    )

    assert status_code == HTTP_OK
    assert actual_payload["source_url"] == expected["source_url"]
    assert actual_payload["detected_type"] == expected["detected_type"]
    assert actual_payload["output_format"] == expected["output_format"]
    _assert_subset(expected["output"], actual_payload["output"])
    assert actual_payload["stats"]["entities_count"] == expected["stats"]["entities_count"]
    assert actual_payload["stats"]["stages_completed"] == expected["stats"]["stages_completed"]
    assert isinstance(actual_payload["warnings"], list)
    assert isinstance(actual_payload["stats"]["duration_ms"], int)
    assert actual_payload["stats"]["run_id"].startswith("pipeline-")
