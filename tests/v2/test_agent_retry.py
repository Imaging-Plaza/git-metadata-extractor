from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents.models import AgentResult
from src.v2.agents.retry import with_retry

INITIAL_RETRY_COUNT = 0
ONE_RETRY = 1
TWO_RETRIES = 2
THREE_ATTEMPTS = 3
TWO_DELAYS = 2


def test_with_retry_passthrough_on_first_success() -> None:
    async def _agent(context: dict[str, Any], _providers: Any) -> AgentResult:
        return AgentResult(data={"id": context["id"]}, warnings=["ok"])

    result = asyncio.run(with_retry(_agent, context={"id": "repo-1"}))

    assert result.data == {"id": "repo-1"}
    assert result.is_partial is False
    assert result.stats["retry_count"] == INITIAL_RETRY_COUNT


def test_with_retry_retries_after_exception_and_returns_success() -> None:
    attempts = {"count": 0}

    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        attempts["count"] += 1
        if attempts["count"] == ONE_RETRY:
            message = "transient failure"
            raise RuntimeError(message)
        return AgentResult(data={"id": "recovered"})

    result = asyncio.run(with_retry(_agent, max_retries=3, backoff_base=0))

    assert attempts["count"] == TWO_RETRIES
    assert result.data == {"id": "recovered"}
    assert result.stats["retry_count"] == ONE_RETRY


def test_with_retry_returns_partial_after_exhaustion() -> None:
    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        message = "hard failure"
        raise ValueError(message)

    result = asyncio.run(with_retry(_agent, max_retries=2, backoff_base=0))

    assert result.is_partial is True
    assert result.failure_reason == "hard failure"
    assert result.stats["retry_count"] == TWO_RETRIES
    assert result.stats["attempts"] == THREE_ATTEMPTS


def test_with_retry_uses_exponential_backoff() -> None:
    delays: list[float] = []

    async def _sleep(delay_seconds: float) -> None:
        delays.append(delay_seconds)

    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        message = "retry me"
        raise RuntimeError(message)

    asyncio.run(
        with_retry(
            _agent,
            max_retries=2,
            backoff_base=0.2,
            sleep_func=_sleep,
        ),
    )

    assert len(delays) == TWO_DELAYS
    assert delays[1] > delays[0]


def test_with_retry_records_retry_stats_on_success() -> None:
    attempts = {"count": 0}

    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        attempts["count"] += 1
        if attempts["count"] < THREE_ATTEMPTS:
            message = "still failing"
            raise RuntimeError(message)
        return AgentResult(data={"id": "stable"})

    result = asyncio.run(with_retry(_agent, max_retries=3, backoff_base=0))

    assert result.stats["attempts"] == THREE_ATTEMPTS
    assert result.stats["retry_count"] == TWO_RETRIES
    assert result.data["id"] == "stable"


def test_with_retry_retries_on_permissive_validation_warning() -> None:
    attempts = {"count": 0}

    async def _agent(_context: dict[str, Any], _providers: Any) -> AgentResult:
        attempts["count"] += 1
        if attempts["count"] == ONE_RETRY:
            return AgentResult(
                data={"id": "invalid-first-pass"},
                warnings=["Validation warning at <root>: invalid payload"],
            )
        return AgentResult(data={"id": "valid-second-pass"})

    result = asyncio.run(with_retry(_agent, max_retries=2, backoff_base=0))

    assert attempts["count"] == TWO_RETRIES
    assert result.data["id"] == "valid-second-pass"
    assert result.stats["retry_count"] == ONE_RETRY
