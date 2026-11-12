"""Schema assets and generated model bindings for v2 extraction."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

#: The hand-written v2 context. Still load-bearing *internally*:
#: `build_jsonld_output` reads it to decide which values serialise as
#: `{"@id": ...}` references, and it describes the v2-shaped intermediate the
#: canonical projection consumes. Not what the response carries any more.
JSONLD_CONTEXT_PATH = Path(__file__).resolve().parent / "json" / "context" / "v2.0.jsonld"

#: The context generated from the v3 SHACL shapes. This is what `/v2/extract`
#: returns, because the graph it describes is the canonical projection.
GENERATED_CONTEXT_PATH = Path(__file__).resolve().parent / "generated" / "context.jsonld"


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


@lru_cache(maxsize=1)
def load_generated_context() -> dict[str, Any]:
    """Return the `@context` generated from the v3 SHACL shapes.

    Falls back to the v2 context when the generated file is absent, so a
    checkout without the ontology submodule still serves requests rather than
    500-ing on every extract. The fallback is wrong-but-working: the graph will
    be v3-shaped while the context describes v2, so prefixed names still expand
    but reference typing is lost.
    """
    if not GENERATED_CONTEXT_PATH.exists():
        return load_jsonld_context()
    payload = json.loads(GENERATED_CONTEXT_PATH.read_text(encoding="utf-8"))
    raw_context = payload.get("@context") if isinstance(payload, dict) else None
    if not isinstance(raw_context, dict):
        message = f"Context file {GENERATED_CONTEXT_PATH} must define '@context'"
        raise TypeError(message)
    return raw_context


__all__ = [
    "GENERATED_CONTEXT_PATH",
    "JSONLD_CONTEXT_PATH",
    "load_generated_context",
    "load_jsonld_context",
]
