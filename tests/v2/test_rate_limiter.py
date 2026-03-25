from __future__ import annotations

import asyncio
import logging

import pytest

from src.v2.ingest.providers.base import ProviderRateLimitError
from src.v2.ingest.providers.rate_limiter import RateLimiter

HTTP_OK = 200
EXPECTED_RETRY_CALLS = 2
EXPECTED_REMAINING_HIGH = 42
EXPECTED_THROTTLE_DELAY = 0.5
EXPECTED_GITHUB_REMAINING = 3
EXPECTED_ROR_REMAINING = 30


class _Response:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class _SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def test_rate_limiter_retries_after_http_429() -> None:
    recorder = _SleepRecorder()
    responses = iter([_Response(429), _Response(200)])
    call_count = 0

    def _request() -> _Response:
        nonlocal call_count
        call_count += 1
        return next(responses)

    limiter = RateLimiter(
        max_retries=2,
        base_delay_seconds=0.1,
        sleep_func=recorder,
        jitter_func=lambda: 0.0,
    )
    response = asyncio.run(limiter.with_rate_limit("github", _request))

    assert response.status_code == HTTP_OK
    assert call_count == EXPECTED_RETRY_CALLS
    assert recorder.delays == [0.1]


def test_rate_limiter_respects_retry_after_header() -> None:
    recorder = _SleepRecorder()
    responses = iter([_Response(429, {"Retry-After": "2"}), _Response(200)])

    limiter = RateLimiter(
        max_retries=2,
        base_delay_seconds=0.1,
        sleep_func=recorder,
        jitter_func=lambda: 0.0,
    )
    asyncio.run(limiter.with_rate_limit("orcid", lambda: next(responses)))

    assert recorder.delays == [2.0]


def test_rate_limiter_tracks_remaining_quota_per_provider() -> None:
    limiter = RateLimiter(jitter_func=lambda: 0.0)
    asyncio.run(
        limiter.with_rate_limit(
            "ror",
            lambda: _Response(200, {"X-RateLimit-Remaining": "42"}),
        ),
    )

    assert limiter.get_remaining("ror") == EXPECTED_REMAINING_HIGH


def test_rate_limiter_throttles_when_remaining_quota_is_low() -> None:
    recorder = _SleepRecorder()
    limiter = RateLimiter(
        low_remaining_threshold=10,
        near_limit_delay_seconds=0.5,
        sleep_func=recorder,
        jitter_func=lambda: 0.0,
    )

    asyncio.run(
        limiter.with_rate_limit(
            "github",
            lambda: _Response(200, {"X-RateLimit-Remaining": "5"}),
        ),
    )
    asyncio.run(
        limiter.with_rate_limit(
            "github",
            lambda: _Response(200),
        ),
    )

    assert any(delay == EXPECTED_THROTTLE_DELAY for delay in recorder.delays)


def test_rate_limiter_raises_after_retry_budget_is_exhausted() -> None:
    recorder = _SleepRecorder()
    limiter = RateLimiter(
        max_retries=2,
        base_delay_seconds=0.1,
        sleep_func=recorder,
        jitter_func=lambda: 0.0,
    )

    with pytest.raises(ProviderRateLimitError):
        asyncio.run(
            limiter.with_rate_limit(
                "infoscience",
                lambda: _Response(429),
            ),
        )

    assert recorder.delays == [0.1, 0.2]


def test_rate_limit_tracking_is_isolated_by_provider() -> None:
    limiter = RateLimiter(jitter_func=lambda: 0.0)
    asyncio.run(
        limiter.with_rate_limit(
            "github",
            lambda: _Response(200, {"X-RateLimit-Remaining": "3"}),
        ),
    )
    asyncio.run(
        limiter.with_rate_limit(
            "ror",
            lambda: _Response(200, {"X-RateLimit-Remaining": "30"}),
        ),
    )

    assert limiter.get_remaining("github") == EXPECTED_GITHUB_REMAINING
    assert limiter.get_remaining("ror") == EXPECTED_ROR_REMAINING


def test_rate_limiter_supports_same_provider_across_multiple_event_loops() -> None:
    limiter = RateLimiter(jitter_func=lambda: 0.0)

    async def _call_once() -> _Response:
        return await limiter.with_rate_limit(
            "github",
            lambda: _Response(200),
        )

    first = asyncio.run(_call_once())
    second = asyncio.run(_call_once())

    assert first.status_code == HTTP_OK
    assert second.status_code == HTTP_OK


def test_rate_limiter_logs_rate_limit_events(caplog: pytest.LogCaptureFixture) -> None:
    recorder = _SleepRecorder()
    responses = iter([_Response(429), _Response(200)])
    limiter = RateLimiter(
        max_retries=1,
        base_delay_seconds=0.1,
        sleep_func=recorder,
        jitter_func=lambda: 0.0,
    )

    with caplog.at_level(logging.WARNING, logger="src.v2.ingest.providers.rate_limiter"):
        asyncio.run(limiter.with_rate_limit("github", lambda: next(responses)))

    assert any("Rate limit retry" in record.message for record in caplog.records)
