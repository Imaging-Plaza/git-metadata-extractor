"""Schema assets and generated model bindings for v2 extraction."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

JSONLD_CONTEXT_PATH = Path(__file__).resolve().parent / "json" / "context" / "v2.0.jsonld"


@lru_cache(maxsize=1)
def load_jsonld_context() -> dict[str, Any]:
    """Return the v2 JSON-LD `@context` mapping."""
    payload = json.loads(JSONLD_CONTEXT_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        message = f"Context file {JSONLD_CONTEXT_PATH} must contain a JSON object"
        raise TypeError(message)
    raw_context = payload.get("@context")
    if not isinstance(raw_context, dict):
        message = f"Context file {JSONLD_CONTEXT_PATH} must define a '@context' object"
        raise ValueError(message)
    return raw_context


__all__ = ["JSONLD_CONTEXT_PATH", "load_jsonld_context"]
