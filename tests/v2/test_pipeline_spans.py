from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Self

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.v2.api import v2_router
from src.v2.observability import agent_instrumentation, middleware, pipeline_spans

HTTP_OK = 200


@dataclass
class _CapturedSpan:
    name: str
    parent: str | None
    attributes: dict[str, Any]


class _FakeSpan:
    def __init__(
        self,
        *,
        logfire: _FakeLogfire,
        span_name: str,
        initial_attributes: dict[str, Any],
    ) -> None:
        self._logfire = logfire
        self._span_name = span_name
        self._initial_attributes = dict(initial_attributes)
        self._captured: _CapturedSpan | None = None

    def __enter__(self) -> Self:
        parent = self._logfire.stack[-1] if self._logfire.stack else None
        self._captured = _CapturedSpan(
            name=self._span_name,
            parent=parent,
            attributes=dict(self._initial_attributes),
        )
        self._logfire.spans.append(self._captured)
        self._logfire.stack.append(self._span_name)
        return self

    def __exit__(self, *_args: object) -> None:
        if self._logfire.stack:
            self._logfire.stack.pop()

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
        self.stack: list[str] = []

    def span(self, span_name: str, **attributes: Any) -> _FakeSpan:
        return _FakeSpan(
            logfire=self,
            span_name=span_name,
            initial_attributes=attributes,
        )


def _extract(path: str) -> tuple[int, Any]:
    async def _run() -> tuple[int, Any]:
        app = FastAPI()
        app.include_router(v2_router)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(path)
        return response.status_code, response.json()

    return asyncio.run(_run())


def test_extract_pipeline_emits_stage_spans_and_agent_children(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(middleware, "_get_logfire_module", lambda: fake_logfire)
    monkeypatch.setattr(pipeline_spans, "_get_logfire_module", lambda: fake_logfire)
    monkeypatch.setattr(agent_instrumentation, "_get_logfire_module", lambda: fake_logfire)

    status_code, _payload = _extract("/v2/extract/github.com/octocat/Hello-World?output_format=json")

    assert status_code == HTTP_OK

    span_names = [span.name for span in fake_logfire.spans]
    assert "pipeline:classify_url" in span_names
    assert "pipeline:context_gather" in span_names
    assert "pipeline:agents" in span_names
    assert "pipeline:permissive_validation" in span_names
    assert "pipeline:strict_validation" in span_names
    assert "pipeline:reconciliation" in span_names
    assert "pipeline:graph_write" in span_names
    assert "pipeline:output_assembly" in span_names

    stage_spans = [span for span in fake_logfire.spans if span.name.startswith("pipeline:")]
    assert stage_spans
    for span in stage_spans:
        assert isinstance(span.attributes.get("duration_ms"), int)
        assert span.attributes.get("status")
        assert span.attributes.get("stage")

    agent_stage_spans = [span for span in fake_logfire.spans if span.name == "pipeline:agents"]
    assert {
        span.attributes.get("orchestrator_stage")
        for span in agent_stage_spans
    } >= {
        "repo_agent",
        "person_agents",
        "org_agents",
        "article_agents",
        "membership_agents",
        "contribution_agents",
    }

    agent_spans = [span for span in fake_logfire.spans if span.name.startswith("agent:")]
    assert agent_spans
    assert all(span.parent == "pipeline:agents" for span in agent_spans)

    validation_stages = {
        span.attributes.get("stage")
        for span in stage_spans
        if "validation" in str(span.attributes.get("stage"))
    }
    assert validation_stages == {"permissive_validation", "strict_validation"}

    request_spans = [span for span in fake_logfire.spans if span.name == "v2.request"]
    assert request_spans
    assert all(span.parent == "v2.request" for span in stage_spans)
