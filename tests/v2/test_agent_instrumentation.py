from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Self

import pytest

from src.v2.agents.models import AgentResult
from src.v2.observability import agent_instrumentation

PROMPT_TOKENS = 13
COMPLETION_TOKENS = 8
RETRY_COUNT = 2
RETRY_PROMPT_TOKENS = 5
RETRY_COMPLETION_TOKENS = 7


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


def _run_agent(agent: Any) -> Any:
    async def _run() -> Any:
        return await agent({}, None)

    return asyncio.run(_run())


def test_instrument_agent_records_success_span_attributes(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(agent_instrumentation, "_get_logfire_module", lambda: fake_logfire)

    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        return AgentResult(
            data={"id": "person-1"},
            model="gpt-4o-mini",
            provider="openai",
            tokens_prompt=PROMPT_TOKENS,
            tokens_completion=COMPLETION_TOKENS,
            stats={"retry_count": 0},
        )

    instrumented = agent_instrumentation.instrument_agent(
        _agent,
        "run-1",
        agent_name="person_agent",
    )
    _run_agent(instrumented)

    assert len(fake_logfire.spans) == 1
    span = fake_logfire.spans[0]
    assert span.name == "agent:person_agent"
    assert span.attributes["model"] == "gpt-4o-mini"
    assert span.attributes["tokens_prompt"] == PROMPT_TOKENS
    assert span.attributes["tokens_completion"] == COMPLETION_TOKENS
    assert span.attributes["status"] == "success"


def test_instrument_agent_records_error_status_and_message(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(agent_instrumentation, "_get_logfire_module", lambda: fake_logfire)

    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        message = "provider failed"
        raise RuntimeError(message)

    instrumented = agent_instrumentation.instrument_agent(
        _agent,
        "run-2",
        agent_name="repo_agent",
    )

    with pytest.raises(RuntimeError, match="provider failed"):
        _run_agent(instrumented)

    span = fake_logfire.spans[0]
    assert span.attributes["status"] == "error"
    assert span.attributes["error_message"] == "provider failed"


def test_instrument_agent_records_retry_count_and_retry_status(
    monkeypatch,
) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(agent_instrumentation, "_get_logfire_module", lambda: fake_logfire)

    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        return AgentResult(
            data={"id": "org-1"},
            stats={
                "retry_count": RETRY_COUNT,
                "prompt_tokens": RETRY_PROMPT_TOKENS,
                "completion_tokens": RETRY_COMPLETION_TOKENS,
            },
        )

    instrumented = agent_instrumentation.instrument_agent(
        _agent,
        "run-3",
        agent_name="org_agent",
    )
    _run_agent(instrumented)

    span = fake_logfire.spans[0]
    assert span.attributes["status"] == "retry"
    assert span.attributes["retry_count"] == RETRY_COUNT
    assert span.attributes["tokens_prompt"] == RETRY_PROMPT_TOKENS
    assert span.attributes["tokens_completion"] == RETRY_COMPLETION_TOKENS


def test_instrument_agent_is_noop_without_logfire(monkeypatch) -> None:
    monkeypatch.setattr(agent_instrumentation, "_get_logfire_module", lambda: None)

    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        return AgentResult(data={"id": "repo"})

    instrumented = agent_instrumentation.instrument_agent(
        _agent,
        "run-4",
        agent_name="repo_agent",
    )
    result = _run_agent(instrumented)

    assert isinstance(result, AgentResult)
    assert result.data == {"id": "repo"}
