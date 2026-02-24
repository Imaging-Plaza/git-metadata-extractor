from __future__ import annotations

from contextlib import nullcontext
from importlib import import_module
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from src.v2.observability.context import RunContext
from src.v2.observability.metrics import V2Metrics

if TYPE_CHECKING:
    from src.v2.agents import ProviderSet
    from src.v2.agents.models import AgentResult

    AgentReturn = AgentResult | dict[str, Any] | Awaitable[AgentResult | dict[str, Any]]
else:
    AgentReturn = Any

AgentRunner = Callable[
    [dict[str, Any], "ProviderSet"],
    AgentReturn,
]


def _get_logfire_module() -> Any | None:
    try:
        logfire_module = import_module("logfire")
    except ImportError:
        return None
    config = getattr(
        getattr(logfire_module, "DEFAULT_LOGFIRE_INSTANCE", None),
        "_config",
        None,
    )
    if getattr(config, "_initialized", False):
        return logfire_module
    return None


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _first_int(mapping: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        numeric_value = _as_int(mapping.get(key))
        if numeric_value is not None:
            return numeric_value
    return None


def _extract_tokens(result: Any) -> tuple[int | None, int | None]:
    prompt_tokens = _as_int(getattr(result, "tokens_prompt", None))
    completion_tokens = _as_int(getattr(result, "tokens_completion", None))

    stats = getattr(result, "stats", None)
    if not isinstance(stats, dict):
        return prompt_tokens, completion_tokens

    if prompt_tokens is None:
        prompt_tokens = _first_int(
            stats,
            ("tokens_prompt", "prompt_tokens", "input_tokens"),
        )

    if completion_tokens is None:
        completion_tokens = _first_int(
            stats,
            ("tokens_completion", "completion_tokens", "output_tokens"),
        )

    usage = stats.get("usage")
    if isinstance(usage, dict):
        if prompt_tokens is None:
            prompt_tokens = _first_int(
                usage,
                ("prompt_tokens", "input_tokens"),
            )
        if completion_tokens is None:
            completion_tokens = _first_int(
                usage,
                ("completion_tokens", "output_tokens"),
            )

    return prompt_tokens, completion_tokens


def _extract_retry_count(result: Any) -> int:
    stats = getattr(result, "stats", None)
    if not isinstance(stats, dict):
        return 0
    retry_count = stats.get("retry_count")
    if isinstance(retry_count, int):
        return retry_count
    return 0


def _extract_model_provider(
    result: Any,
    context: dict[str, Any],
    *,
    default_model: str | None,
    default_provider: str | None,
) -> tuple[str | None, str | None]:
    model = default_model
    provider = default_provider

    if model is None:
        model_candidate = getattr(result, "model", None)
        if isinstance(model_candidate, str) and model_candidate:
            model = model_candidate
    if model is None:
        context_model = context.get("model")
        if isinstance(context_model, str) and context_model:
            model = context_model

    if provider is None:
        provider_candidate = getattr(result, "provider", None)
        if isinstance(provider_candidate, str) and provider_candidate:
            provider = provider_candidate
    if provider is None:
        context_provider = context.get("provider")
        if isinstance(context_provider, str) and context_provider:
            provider = context_provider

    return model, provider


def _set_span_attributes(span: Any, **attributes: Any) -> None:
    single_setter = getattr(span, "set_attribute", None)
    if callable(single_setter):
        for key, value in attributes.items():
            single_setter(key, value)
        return

    multi_setter = getattr(span, "set_attributes", None)
    if callable(multi_setter):
        try:
            multi_setter(dict(attributes))
        except TypeError:
            multi_setter(**attributes)


def instrument_agent(
    agent: AgentRunner,
    run_id: str | None,
    *,
    agent_name: str,
    model: str | None = None,
    provider: str | None = None,
) -> AgentRunner:
    """Wrap an agent call with a structured Logfire span."""
    metrics = V2Metrics()

    async def _instrumented(
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult | dict[str, Any]:
        logfire_module = _get_logfire_module()
        resolved_run_id = run_id or RunContext.get_run_id() or None
        span_context = (
            logfire_module.span(
                f"agent:{agent_name}",
                agent_name=agent_name,
                run_id=resolved_run_id,
            )
            if logfire_module is not None
            else nullcontext()
        )

        with span_context as span:
            try:
                result = await _maybe_await(agent(context, providers))
            except Exception as exc:
                if logfire_module is not None and span is not None:
                    _set_span_attributes(
                        span,
                        status="error",
                        error_class=exc.__class__.__name__,
                        error_message=str(exc),
                    )
                raise

            status = "success"
            if getattr(result, "is_partial", False):
                status = "failure"
            elif _extract_retry_count(result) > 0:
                status = "retry"

            tokens_prompt, tokens_completion = _extract_tokens(result)
            resolved_model, resolved_provider = _extract_model_provider(
                result,
                context,
                default_model=model,
                default_provider=provider,
            )

            if logfire_module is not None and span is not None:
                _set_span_attributes(
                    span,
                    status=status,
                    model=resolved_model,
                    provider=resolved_provider,
                    tokens_prompt=tokens_prompt,
                    tokens_completion=tokens_completion,
                    retry_count=_extract_retry_count(result),
                )
                if status == "failure":
                    _set_span_attributes(
                        span,
                        error_message=getattr(result, "failure_reason", None),
                    )

            metrics.record_tokens(
                agent_name=agent_name,
                prompt_tokens=tokens_prompt,
                completion_tokens=tokens_completion,
            )

            return result

    return _instrumented
