from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

from src.v2.observability.context import RunContext
from src.v2.observability.log_filter import RunIdLogFilter

logger = logging.getLogger(__name__)
_RUN_ID_FILTER = RunIdLogFilter()
if not any(isinstance(log_filter, RunIdLogFilter) for log_filter in logger.filters):
    logger.addFilter(_RUN_ID_FILTER)


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


def record_error(  # noqa: PLR0913
    stage: str,
    error: Exception,
    *,
    run_id: str | None = None,
    source_url: str | None = None,
    detected_type: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    resolved_run_id = run_id or RunContext.get_run_id() or None
    attributes = {
        "stage": stage,
        "error_class": error.__class__.__name__,
        "error_message": str(error),
        "run_id": resolved_run_id,
        "source_url": source_url,
        "detected_type": detected_type,
    }
    if details:
        attributes.update(details)

    logfire_module = _get_logfire_module()
    if logfire_module is not None:
        logfire_module.error("v2.error", **attributes)

    logger.error(
        "v2 error stage=%s class=%s message=%s",
        stage,
        error.__class__.__name__,
        str(error),
        extra=attributes,
    )
