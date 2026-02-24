from __future__ import annotations

import inspect
from contextlib import contextmanager, nullcontext
from importlib import import_module
from time import perf_counter
from typing import Any, Callable, Generator, TypeVar, cast

T = TypeVar("T")


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


class _SpanProxy:
    def __init__(self, span: Any | None) -> None:
        self._span = span
        self._status_explicit = False

    def set_attribute(self, key: str, value: Any) -> None:
        if key == "status":
            self._status_explicit = True
        if self._span is None:
            return
        setter = getattr(self._span, "set_attribute", None)
        if callable(setter):
            setter(key, value)

    def set_attributes(self, **attributes: Any) -> None:
        if "status" in attributes:
            self._status_explicit = True
        if self._span is None:
            return
        setter = getattr(self._span, "set_attributes", None)
        if callable(setter):
            try:
                setter(dict(attributes))
            except TypeError:
                try:
                    setter(**attributes)
                except TypeError:
                    pass
                else:
                    return
            else:
                return
        single_setter = getattr(self._span, "set_attribute", None)
        if callable(single_setter):
            for key, value in attributes.items():
                single_setter(key, value)


class PipelineTracer:
    def __init__(self, *, run_id: str | None = None) -> None:
        self._run_id = run_id

    @contextmanager
    def trace_stage(self, stage_name: str, **attributes: Any) -> Generator[_SpanProxy]:
        started_at = perf_counter()
        logfire_module = _get_logfire_module()

        span_context = (
            logfire_module.span(
                f"pipeline:{stage_name}",
                stage=stage_name,
                run_id=self._run_id,
                **attributes,
            )
            if logfire_module is not None
            else nullcontext()
        )

        with span_context as span:
            proxy = _SpanProxy(span)
            try:
                yield proxy
            except Exception as exc:
                proxy.set_attributes(
                    status="error",
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    error_class=exc.__class__.__name__,
                    error_message=str(exc),
                )
                raise
            else:
                duration_ms = int((perf_counter() - started_at) * 1000)
                if proxy._status_explicit:  # noqa: SLF001
                    proxy.set_attribute("duration_ms", duration_ms)
                else:
                    proxy.set_attributes(
                        status="success",
                        duration_ms=duration_ms,
                    )

    def child(self, *, run_id: str | None = None) -> PipelineTracer:
        return PipelineTracer(run_id=run_id or self._run_id)


def trace_stage(
    stage_name: str,
    *,
    tracer: PipelineTracer,
    **attributes: Any,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator helper for instrumenting stage callables."""

    def _decorator(func: Callable[..., T]) -> Callable[..., T]:
        if inspect.iscoroutinefunction(func):

            async def _async_wrapped(*args: Any, **kwargs: Any) -> T:
                with tracer.trace_stage(stage_name, **attributes):
                    return await cast("Any", func)(*args, **kwargs)

            return cast("Callable[..., T]", _async_wrapped)

        def _wrapped(*args: Any, **kwargs: Any) -> T:
            with tracer.trace_stage(stage_name, **attributes):
                return func(*args, **kwargs)

        return _wrapped

    return _decorator
