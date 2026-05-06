"""GIMIE JSON-LD route maps GitHub ConnectionError to 429/503."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.v1.utils.github_dependency import validate_github_token


async def _github_info_override() -> dict:
    return {
        "valid": True,
        "rate_limit_limit": 5000,
        "rate_limit_remaining": 4999,
        "rate_limit_reset": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


@pytest.fixture
def gimie_client() -> TestClient:
    app.dependency_overrides[validate_github_token] = _github_info_override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("message", "expected_status"),
    [
        (
            "API request failed: You have exceeded a secondary rate limit. Wait.",
            429,
        ),
        (
            "API request failed: rate limit exceeded",
            429,
        ),
        (
            "API request failed: API rate limit exceeded",
            429,
        ),
        (
            "API request failed: connection reset by peer",
            503,
        ),
    ],
)
def test_gimie_connectionerror_status(
    gimie_client: TestClient,
    message: str,
    expected_status: int,
) -> None:
    with patch("src.api.Repository") as repo_cls:
        inst = repo_cls.return_value
        inst.run_analysis = AsyncMock(side_effect=ConnectionError(message))

        path = "https%3A%2F%2Fgithub.com%2Fowner%2Frepo"
        r = gimie_client.get(f"/v1/repository/gimie/json-ld/{path}")

    assert r.status_code == expected_status
    body = r.json()
    assert body.get("detail") == message
