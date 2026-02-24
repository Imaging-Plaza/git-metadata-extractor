from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Self
from uuid import UUID

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.agents import ProviderSet
from src.v2.api import v2_router
from src.v2.graph.store import GraphStore
from src.v2.observability import agent_instrumentation, middleware, pipeline_spans
from src.v2.observability.context import RunContext
from src.v2.observability.log_filter import RunIdLogFilter
from src.v2.observability.middleware import V2TracingMiddleware
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.providers.mock_orcid import MockORCIDProvider
from src.v2.providers.mock_ror import MockRORProvider

HTTP_OK = 200


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

    def set_attributes(self, values: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if self._captured is None:
            return
        if values:
            self._captured.attributes.update(values)
        if kwargs:
            self._captured.attributes.update(kwargs)


class _FakeLogfire:
    def __init__(self) -> None:
        self.spans: list[_CapturedSpan] = []

    def span(self, span_name: str, **attributes: Any) -> _FakeSpan:
        return _FakeSpan(
            span_name=span_name,
            initial_attributes=attributes,
            sink=self.spans,
        )


def _build_provider_set() -> ProviderSet:
    return ProviderSet(
        github=MockGitHubProvider(),
        orcid=MockORCIDProvider(),
        infoscience=MockInfoscienceProvider(),
        ror=MockRORProvider(),
    )


def test_request_run_id_matches_spans_and_logs(monkeypatch, caplog) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(middleware, "_get_logfire_module", lambda: fake_logfire)

    logger = logging.getLogger("tests.v2.runid.correlation")
    logger.addFilter(RunIdLogFilter())
    caplog.set_level("INFO", logger=logger.name)

    app = FastAPI()
    router = APIRouter(prefix="/v2", route_class=V2TracingMiddleware)

    @router.get("/runid")
    async def runid() -> dict[str, str]:
        logger.info("request log with run id")
        return {"run_id": RunContext.get_run_id()}

    app.include_router(router)

    async def _request() -> tuple[int, dict[str, str], dict[str, Any]]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/v2/runid")
        return response.status_code, dict(response.headers), response.json()

    status_code, headers, payload = asyncio.run(_request())

    assert status_code == HTTP_OK
    header_run_id = headers["x-run-id"]
    assert str(UUID(header_run_id)) == header_run_id
    assert payload["run_id"] == header_run_id
    assert fake_logfire.spans[0].attributes["run_id"] == header_run_id
    assert caplog.records[-1].run_id == header_run_id


def test_concurrent_requests_receive_distinct_run_ids(monkeypatch) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(middleware, "_get_logfire_module", lambda: fake_logfire)

    app = FastAPI()
    router = APIRouter(prefix="/v2", route_class=V2TracingMiddleware)

    @router.get("/runid")
    async def runid() -> dict[str, str]:
        return {"run_id": RunContext.get_run_id()}

    app.include_router(router)

    async def _request() -> tuple[str, str]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/v2/runid")
        return response.headers["x-run-id"], response.json()["run_id"]

    async def _run_concurrent() -> tuple[tuple[str, str], tuple[str, str]]:
        first, second = await asyncio.gather(_request(), _request())
        return first, second

    first, second = asyncio.run(_run_concurrent())
    assert first[0] == first[1]
    assert second[0] == second[1]
    assert first[0] != second[0]


def test_extract_run_id_matches_header_spans_and_run_record(
    monkeypatch,
    tmp_path,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(middleware, "_get_logfire_module", lambda: fake_logfire)
    monkeypatch.setattr(pipeline_spans, "_get_logfire_module", lambda: fake_logfire)
    monkeypatch.setattr(agent_instrumentation, "_get_logfire_module", lambda: fake_logfire)
    monkeypatch.setenv("V2_GRAPH_DB_PATH", str(tmp_path / "v2_graph.db"))

    app = FastAPI()
    app.include_router(v2_router)
    app.state.v2_provider_set = _build_provider_set()

    async def _request() -> tuple[int, dict[str, str], dict[str, Any]]:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/v2/extract/github.com/octocat/Hello-World",
                params={"output_format": "json"},
            )
        return response.status_code, dict(response.headers), response.json()

    status_code, headers, payload = asyncio.run(_request())
    header_run_id = headers["x-run-id"]

    assert status_code == HTTP_OK
    assert str(UUID(header_run_id)) == header_run_id
    assert payload["stats"]["run_id"] == header_run_id

    store = GraphStore(str(tmp_path / "v2_graph.db"))
    run_record = store.get_run(header_run_id)
    assert run_record is not None
    assert run_record.id == header_run_id

    span_run_ids = {
        span.attributes.get("run_id")
        for span in fake_logfire.spans
        if span.attributes.get("run_id")
    }
    assert span_run_ids == {header_run_id}
