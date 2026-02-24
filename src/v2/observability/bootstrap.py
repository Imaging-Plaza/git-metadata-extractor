from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

    from src.v2.config import V2Config

logger = logging.getLogger(__name__)


def _get_logfire_module() -> Any | None:
    try:
        return import_module("logfire")
    except ImportError:
        return None


def initialize_logfire(config: V2Config, *, app: FastAPI | None = None) -> bool:
    """Initialize Logfire for v2 execution paths.

    Returns ``True`` when configuration succeeds, otherwise ``False``.
    """

    if not config.V2_ENABLE_LOGFIRE:
        logger.info("V2 Logfire disabled via V2_ENABLE_LOGFIRE=false")
        return False

    logfire_module = _get_logfire_module()
    if logfire_module is None:
        logger.warning(
            "V2 Logfire enabled but logfire is unavailable; continuing without telemetry.",
        )
        return False

    configure_kwargs: dict[str, Any] = {
        "send_to_logfire": "if-token-present",
    }
    if config.LOGFIRE_TOKEN:
        configure_kwargs["token"] = config.LOGFIRE_TOKEN

    try:
        logfire_module.configure(**configure_kwargs)
        if app is not None:
            logfire_module.instrument_fastapi(app)
        logfire_module.instrument_pydantic_ai()
    except Exception:
        logger.exception("V2 Logfire bootstrap failed; continuing without telemetry.")
        return False

    logger.info(
        "V2 Logfire initialized (fastapi_instrumented=%s token_present=%s)",
        app is not None,
        bool(config.LOGFIRE_TOKEN),
    )
    return True
