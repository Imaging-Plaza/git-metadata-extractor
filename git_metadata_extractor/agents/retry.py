from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Awaitable, Callable, cast

from git_metadata_extractor.agents.models import AgentResult, ProviderSet

VALIDATION_WARNING_PREFIX = "Validation warning at"

SleepCallable = Callable[[float], Awaitable[None]]
InvalidResultChecker = Callable[[AgentResult], str | None]
AgentRunner = Callable[
    [dict[str, Any], ProviderSet | None],
    AgentResult | dict[str, Any] | Awaitable[AgentResult | dict[str, Any]],
]


def _append_unique(warnings: list[str], warning: str) -> None:
    if warning and warning not in warnings:
        warnings.append(warning)


def _resolve_runner(agent: Any) -> AgentRunner:
    if callable(agent):
        return cast("AgentRunner", agent)

    run_method = getattr(agent, "run", None)
    if callable(run_method):
        return cast("AgentRunner", run_method)

    message = "Agent retry wrapper expects a callable or an object exposing .run()"
    raise TypeError(message)


def _default_invalid_reason(result: AgentResult) -> str | None:
    if result.is_partial:
        return result.failure_reason or "Agent returned partial result"

    for warning in result.warnings:
        if warning.startswith(VALIDATION_WARNING_PREFIX):
            return "Agent output did not satisfy permissive validation"

    if not isinstance(result.data, dict) or not result.data:
        return "Agent returned empty or invalid data payload"

    return None


def _normalize_result(candidate: AgentResult | dict[str, Any]) -> AgentResult:
    if isinstance(candidate, AgentResult):
        return candidate
    if isinstance(candidate, dict):
        return AgentResult(data=candidate)
    message = "Agent runner must return AgentResult or dict payload"
    raise TypeError(message)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def with_retry(  # noqa: C901, PLR0913
    agent: Any,
    *,
    context: dict[str, Any] | None = None,
    providers: ProviderSet | None = None,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    sleep_func: SleepCallable | None = None,
    invalid_result_checker: InvalidResultChecker | None = None,
) -> AgentResult:
    """Execute an agent with retries and soft-failure semantics."""
    if max_retries < 0:
        raise ValueError
    if backoff_base < 0:
        raise ValueError

    runner = _resolve_runner(agent)
    sleep = sleep_func or asyncio.sleep
    is_invalid = invalid_result_checker or _default_invalid_reason

    warnings: list[str] = []
    errors: list[str] = []
    backoff_schedule: list[float] = []
    attempt_count = max_retries + 1

    last_result: AgentResult | None = None
    failure_reason: str | None = None

    for attempt_index in range(attempt_count):
        try:
            attempt_context = deepcopy(context or {})
            candidate = await _maybe_await(runner(attempt_context, providers))
            result = _normalize_result(candidate)
            last_result = result

            for warning in result.warnings:
                _append_unique(warnings, warning)

            invalid_reason = is_invalid(result)
            if invalid_reason is None:
                result.stats = {
                    **result.stats,
                    "attempts": attempt_index + 1,
                    "retry_count": attempt_index,
                    "backoff_schedule": backoff_schedule,
                }
                return result

            failure_reason = invalid_reason
            _append_unique(warnings, invalid_reason)
        except Exception as exc:  # noqa: BLE001
            failure_reason = str(exc)
            errors.append(str(exc))
            _append_unique(warnings, f"Agent attempt {attempt_index + 1} failed: {exc}")

        if attempt_index == max_retries:
            break

        delay_seconds = backoff_base * (2**attempt_index)
        backoff_schedule.append(delay_seconds)
        if delay_seconds > 0:
            await sleep(delay_seconds)

    if last_result is not None:
        partial_data = dict(last_result.data)
        partial_raw_output = dict(last_result.raw_output)
    else:
        partial_data = {}
        partial_raw_output = {}

    if errors:
        _append_unique(warnings, f"Agent retry exhausted after {attempt_count} attempts")

    return AgentResult(
        data=partial_data,
        warnings=warnings,
        raw_output=partial_raw_output,
        is_partial=True,
        failure_reason=failure_reason or "Agent execution failed",
        stats={
            "attempts": attempt_count,
            "retry_count": max_retries,
            "backoff_schedule": backoff_schedule,
            "errors": errors,
        },
    )
