from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, TypeVar

from git_metadata_extractor.providers.base import ProviderRateLimitError

ResponseT = TypeVar("ResponseT")
RequestFunc = Callable[[], ResponseT | Awaitable[ResponseT]]

LOGGER = logging.getLogger(__name__)
HTTP_RATE_LIMIT = 429


@dataclass(slots=True)
class _ProviderRateLimitState:
    remaining: int | None = None
    reset_epoch: float | None = None
    next_allowed_at: float = 0.0


class RateLimiter:
    def __init__(  # noqa: PLR0913
        self,
        *,
        max_retries: int = 3,
        base_delay_seconds: float = 0.5,
        max_delay_seconds: float = 30.0,
        low_remaining_threshold: int = 10,
        near_limit_delay_seconds: float = 0.25,
        sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter_func: Callable[[], float] | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._low_remaining_threshold = low_remaining_threshold
        self._near_limit_delay_seconds = near_limit_delay_seconds
        self._sleep = sleep_func
        self._jitter = jitter_func or random.random
        self._states: dict[str, _ProviderRateLimitState] = {}
        # asyncio.Lock instances are bound to the loop where they are created.
        # Keep separate locks per provider+loop to avoid cross-loop binding errors.
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}

    def get_remaining(self, provider_name: str) -> int | None:
        return self._state(provider_name).remaining

    async def with_rate_limit(
        self,
        provider_name: str,
        request_func: RequestFunc[ResponseT],
    ) -> ResponseT:
        normalized_provider = provider_name.strip().lower() or "unknown"
        current_loop = asyncio.get_running_loop()
        lock_key = (normalized_provider, id(current_loop))
        provider_lock = self._locks.setdefault(lock_key, asyncio.Lock())
        async with provider_lock:
            await self._throttle_when_approaching_limit(normalized_provider)

            for attempt in range(self._max_retries + 1):
                try:
                    response = request_func()
                    if hasattr(response, "__await__"):
                        response = await response
                except ProviderRateLimitError as exc:
                    if attempt >= self._max_retries:
                        raise
                    delay = self._backoff_delay(attempt, retry_after=None)
                    await self._schedule_retry(normalized_provider, delay, str(exc))
                    continue

                self._record_quota_headers(normalized_provider, response)
                status_code = self._response_status_code(response)
                if status_code != HTTP_RATE_LIMIT:
                    return response

                retry_after = self._retry_after_seconds(response)
                if attempt >= self._max_retries:
                    message = (
                        f"{normalized_provider} rate limit exceeded after "
                        f"{self._max_retries + 1} attempts"
                    )
                    LOGGER.warning(message)
                    raise ProviderRateLimitError(message)

                delay = self._backoff_delay(attempt, retry_after=retry_after)
                await self._schedule_retry(normalized_provider, delay, "HTTP 429")

        message = f"{normalized_provider} rate limit exceeded"
        raise ProviderRateLimitError(message)

    def _state(self, provider_name: str) -> _ProviderRateLimitState:
        normalized_provider = provider_name.strip().lower() or "unknown"
        return self._states.setdefault(normalized_provider, _ProviderRateLimitState())

    async def _throttle_when_approaching_limit(self, provider_name: str) -> None:
        state = self._state(provider_name)
        now_epoch = time.time()

        if state.next_allowed_at > now_epoch:
            wait_seconds = state.next_allowed_at - now_epoch
            LOGGER.info(
                "Rate limit queue wait for %s: %.3fs",
                provider_name,
                wait_seconds,
            )
            await self._sleep(wait_seconds)

        if state.remaining is None or state.remaining >= self._low_remaining_threshold:
            return

        throttle_delay = self._near_limit_delay_seconds
        if (
            state.reset_epoch is not None
            and state.reset_epoch > now_epoch
            and throttle_delay > (state.reset_epoch - now_epoch)
        ):
            throttle_delay = state.reset_epoch - now_epoch

        if throttle_delay <= 0:
            return

        LOGGER.info(
            "Proactive throttle for %s (remaining=%s): %.3fs",
            provider_name,
            state.remaining,
            throttle_delay,
        )
        await self._sleep(throttle_delay)

    async def _schedule_retry(
        self,
        provider_name: str,
        delay_seconds: float,
        reason: str,
    ) -> None:
        state = self._state(provider_name)
        state.next_allowed_at = max(state.next_allowed_at, time.time() + delay_seconds)
        LOGGER.warning(
            "Rate limit retry for %s in %.3fs (%s)",
            provider_name,
            delay_seconds,
            reason,
        )
        await self._sleep(delay_seconds)

    def _backoff_delay(self, attempt: int, *, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(self._max_delay_seconds, max(0.0, retry_after))

        exponential_delay = self._base_delay_seconds * (2**attempt)
        jitter = self._jitter() * self._base_delay_seconds
        return min(self._max_delay_seconds, exponential_delay + jitter)

    def _record_quota_headers(self, provider_name: str, response: Any) -> None:
        headers = self._headers(response)
        if not headers:
            return

        state = self._state(provider_name)
        remaining = self._parse_int_header(headers.get("x-ratelimit-remaining"))
        if remaining is not None:
            state.remaining = remaining

        reset_epoch = self._parse_reset_header(headers.get("x-ratelimit-reset"))
        if reset_epoch is not None:
            state.reset_epoch = reset_epoch

        retry_after = self._retry_after_seconds(response)
        if retry_after is not None:
            state.next_allowed_at = max(state.next_allowed_at, time.time() + retry_after)

    @staticmethod
    def _response_status_code(response: Any) -> int | None:
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code
        return None

    @staticmethod
    def _headers(response: Any) -> dict[str, str]:
        headers = getattr(response, "headers", None)
        if headers is None:
            return {}
        if isinstance(headers, dict):
            return {
                str(key).lower(): str(value)
                for key, value in headers.items()
            }
        if hasattr(headers, "items"):
            return {
                str(key).lower(): str(value)
                for key, value in headers.items()
            }
        return {}

    def _retry_after_seconds(self, response: Any) -> float | None:
        headers = self._headers(response)
        return self._parse_retry_after_header(headers.get("retry-after"))

    @staticmethod
    def _parse_int_header(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_reset_header(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_retry_after_header(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
            return max(0.0, parsed.timestamp() - time.time())
