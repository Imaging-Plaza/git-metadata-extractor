from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Self
from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router
from src.v2.observability import middleware
from src.v2.observability.middleware import V2TracingMiddleware

HTTP_OK = 200
HTTP_BAD_REQUEST = 400


@dataclass
class _CapturedSpan:
    name: str
    attributes: dict[str, Any]


class _FakeSpan:
    def __init__(
        self,
        *,
        span_name: str,
        initial_attributes: dict[str, Any],
        sink: list[_CapturedSpan],
    ) -> None:
        self._span_name = span_name
        self._initial_attributes = dict(initial_attributes)
        self._sink = sink
        self._captured: _CapturedSpan | None = None

    def __enter__(self) -> Self:
        self._captured = _CapturedSpan(
            name=self._span_name,
            attributes=dict(self._initial_attributes),
        )
        self._sink.append(self._captured)
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        if self._captured is None:
            return
        self._captured.attributes[key] = value

    def set_attributes(self, **attributes: Any) -> None:
        if self._captured is None:
            return
        self._captured.attributes.update(attributes)


class _FakeLogfire:
    def __init__(self) -> None:
        self.spans: list[_CapturedSpan] = []

    def span(self, span_name: str, **attributes: Any) -> _FakeSpan:
        return _FakeSpan(
            span_name=span_name,
            initial_attributes=attributes,
            sink=self.spans,
        )


def _request(
    app: FastAPI,
    path: str,
) -> tuple[int, dict[str, str], Any]:
    async def _run() -> tuple[int, dict[str, str], Any]:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path)
        return response.status_code, dict(response.headers), response.text

    return asyncio.run(_run())


def test_v2_router_request_adds_run_id_and_span_attributes(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(middleware, "_get_logfire_module", lambda: fake_logfire)

    app = FastAPI()
    app.include_router(v2_router)

    status_code, headers, _ = _request(app, "/v2/health")

    assert status_code == HTTP_OK
    run_id = headers["x-run-id"]
    assert str(UUID(run_id)) == run_id

    assert len(fake_logfire.spans) == 1
    span = fake_logfire.spans[0]
    assert span.name == "v2.request"
    assert span.attributes["path"] == "/v2/health"
    assert span.attributes["method"] == "GET"
    assert span.attributes["status_code"] == HTTP_OK
    assert isinstance(span.attributes["duration_ms"], int)
    assert span.attributes["duration_ms"] >= 0


def test_non_v2_routes_remain_unaffected(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(middleware, "_get_logfire_module", lambda: fake_logfire)

    app = FastAPI()
    app.include_router(v2_router)

    @app.get("/v1/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    status_code, headers, _ = _request(app, "/v1/ping")

    assert status_code == HTTP_OK
    assert "x-run-id" not in headers
    assert fake_logfire.spans == []


def test_streaming_response_is_not_broken_by_v2_tracing(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(middleware, "_get_logfire_module", lambda: fake_logfire)

    app = FastAPI()
    router = APIRouter(prefix="/v2", route_class=V2TracingMiddleware)

    @router.get("/stream")
    async def stream() -> StreamingResponse:
        async def _chunks():
            yield "a"
            yield "b"

        return StreamingResponse(_chunks(), media_type="text/plain")

    app.include_router(router)

    status_code, headers, response_text = _request(app, "/v2/stream")

    assert status_code == HTTP_OK
    assert response_text == "ab"
    assert str(UUID(headers["x-run-id"])) == headers["x-run-id"]


def test_error_response_keeps_status_and_run_id_header(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(middleware, "_get_logfire_module", lambda: fake_logfire)

    app = FastAPI()
    router = APIRouter(prefix="/v2", route_class=V2TracingMiddleware)

    @router.get("/error")
    async def error() -> dict[str, str]:
        message = "bad request"
        raise HTTPException(status_code=HTTP_BAD_REQUEST, detail=message)

    app.include_router(router)

    status_code, headers, _ = _request(app, "/v2/error")

    assert status_code == HTTP_BAD_REQUEST
    assert str(UUID(headers["x-run-id"])) == headers["x-run-id"]
    assert fake_logfire.spans[0].attributes["status_code"] == HTTP_BAD_REQUEST
