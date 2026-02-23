from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router
from src.v2.models.contracts import V2ExtractResponse

HTTP_OK = 200
HTTP_UNPROCESSABLE_ENTITY = 422


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(v2_router)
    return app


def _get_json(path: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        test_app = _build_test_app()
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path, params=params)
        return response.status_code, response.json()

    return asyncio.run(_run())


def test_extract_repository_url_returns_detected_repository() -> None:
    status_code, payload = _get_json("/v2/extract/github.com/owner/repo")

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "repository"
    assert V2ExtractResponse.model_validate(payload)


def test_extract_user_url_returns_detected_user() -> None:
    status_code, payload = _get_json("/v2/extract/github.com/username")

    assert status_code == HTTP_OK
    assert payload["detected_type"] == "user"
    assert V2ExtractResponse.model_validate(payload)


def test_extract_unsupported_issue_url_returns_typed_422() -> None:
    status_code, payload = _get_json("/v2/extract/github.com/owner/repo/issues/1")

    assert status_code == HTTP_UNPROCESSABLE_ENTITY
    assert payload["error_type"] == "unsupported_url"
    assert payload["detected_path_kind"] == "issues"


def test_extract_accepts_output_format_json() -> None:
    status_code, payload = _get_json(
        "/v2/extract/github.com/owner/repo",
        params={"output_format": "json"},
    )

    assert status_code == HTTP_OK
    assert payload["output_format"] == "json"


def test_extract_rejects_invalid_output_format() -> None:
    status_code, payload = _get_json(
        "/v2/extract/github.com/owner/repo",
        params={"output_format": "xml"},
    )

    assert status_code == HTTP_UNPROCESSABLE_ENTITY
    assert "detail" in payload
