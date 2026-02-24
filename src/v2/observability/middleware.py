from __future__ import annotations

from contextlib import nullcontext
from importlib import import_module
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from src.v2.observability.context import RunContext

RUN_ID_HEADER = "X-Run-Id"


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


def _response_size(response: Response) -> int | None:
    content_length = response.headers.get("content-length")
    if content_length is None:
        return None
    try:
        return int(content_length)
    except ValueError:
        return None


def _set_span_attributes(span: Any, **attributes: Any) -> None:
    setter = getattr(span, "set_attribute", None)
    if callable(setter):
        for key, value in attributes.items():
            setter(key, value)
        return

    multi_setter = getattr(span, "set_attributes", None)
    if callable(multi_setter):
        try:
            multi_setter(dict(attributes))
        except TypeError:
            multi_setter(**attributes)


class V2TracingMiddleware(APIRoute):
    """Route-level tracing wrapper for v2 endpoints."""

    def get_route_handler(self):  # type: ignore[override]
        original_handler = super().get_route_handler()

        async def traced_route_handler(request: Request) -> Response:
            run_id = str(uuid4())
            request.state.v2_run_id = run_id
            context_token = RunContext.set_run_id(run_id)
            started_at = perf_counter()

            logfire_module = _get_logfire_module()
            span_context = (
                logfire_module.span(
                    "v2.request",
                    path=request.url.path,
                    method=request.method,
                )
                if logfire_module is not None
                else nullcontext()
            )

            with span_context as span:
                try:
                    try:
                        response = await original_handler(request)
                    except HTTPException as exc:
                        response = JSONResponse(
                            status_code=exc.status_code,
                            content={"detail": exc.detail},
                            headers=exc.headers,
                        )
                    final_run_id = getattr(request.state, "v2_run_id", None)
                    if not isinstance(final_run_id, str) or not final_run_id:
                        final_run_id = RunContext.get_run_id()
                    response.headers[RUN_ID_HEADER] = final_run_id

                    duration_ms = int((perf_counter() - started_at) * 1000)
                    if logfire_module is not None and span is not None:
                        _set_span_attributes(
                            span,
                            run_id=final_run_id,
                            status_code=response.status_code,
                            duration_ms=duration_ms,
                            response_size=_response_size(response),
                        )
                    return response
                finally:
                    RunContext.reset(context_token)

        return traced_route_handler
