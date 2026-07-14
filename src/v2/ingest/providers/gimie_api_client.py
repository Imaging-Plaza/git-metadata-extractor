"""HTTP client for the ``gimie-api`` sidecar (``ghcr.io/sdsc-ordes/gimie-api``).

A drop-in replacement for the in-process ``extract_gimie`` that calls a sidecar
service running the same gimie version, so the heavy ``gimie`` (+ ``calamus``)
dependency can eventually leave this project's Python tree. Enabled by setting
``GIMIE_API_URL`` (e.g. ``http://gme-gimie-api:15400``); when unset, callers fall
back to in-process gimie.

The sidecar's only data routes are ``GET /gimie/ttl/{full_path}`` (Turtle) and
``GET /gimie/project/{full_path}`` (a Python repr — not machine-usable). There
is **no JSON-LD route** (task brief 11: the previously-assumed
``/gimie/jsonld/`` never existed upstream), so for
``serialization_format="json-ld"`` this client fetches the TTL and converts it
with rdflib — exactly mirroring the in-process reference
(``json.loads(graph.serialize(format="json-ld"))`` in
`the retired v1 gimie module (now `gimie_extract.py`)`), which keeps the payload shape
byte-compatible with what every downstream consumer was built against.

Responses come as ``{"link": <url>, "output": "<ttl string>"}``. NOTE the
sidecar's error contract: failures come back as **HTTP 200** with ``output``
set to the error *message*, which won't parse as Turtle → we degrade to
``None``, matching the in-process extractor's behaviour.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests
from rdflib import Graph as RDFGraph

logger = logging.getLogger(__name__)

_HTTP_OK = 200
_DEFAULT_TIMEOUT_SECONDS = 180.0  # gimie extraction is slow (many GitHub calls).
_ERROR_OUTPUT_PREVIEW = 200


def gimie_api_base() -> str | None:
    """Return the configured gimie-api base URL (no trailing slash), or None
    when ``GIMIE_API_URL`` is unset/blank (→ callers use in-process gimie)."""
    base = os.getenv("GIMIE_API_URL", "").strip()
    return base.rstrip("/") or None


def _timeout() -> float:
    raw = os.getenv("GIMIE_API_TIMEOUT_SECONDS", "").strip()
    try:
        return float(raw) if raw else _DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def extract_gimie_via_api(  # noqa: PLR0911 — guard-heavy network fetch; flat returns read clearer
    full_path: str,
    serialization_format: str = "json-ld",
    *,
    session: Any = None,
) -> Any:
    """Fetch a repo's gimie metadata from the gimie-api sidecar.

    Signature-compatible with ``src.v2.ingest.providers.gimie_extract.extract_gimie``:
    returns the parsed JSON-LD (rdflib expanded form — a list of node dicts)
    for ``serialization_format="json-ld"``, the TTL string for ``"ttl"``, or
    ``None`` on any failure (sidecar down, non-200, the HTTP-200 error
    contract, or a response that doesn't parse as Turtle).
    """
    base = gimie_api_base()
    if base is None:
        return None
    http = session if session is not None else requests
    # The sidecar's only machine-readable route is /gimie/ttl/ — JSON-LD is
    # produced client-side from it (see module docstring).
    url = f"{base}/gimie/ttl/{full_path}"

    try:
        resp = http.get(url, timeout=_timeout())
    except Exception:  # noqa: BLE001 — best-effort; degrade to None like in-process.
        logger.warning("gimie-api request failed: %s", full_path)
        return None

    if getattr(resp, "status_code", None) != _HTTP_OK:
        logger.warning(
            "gimie-api returned %s for %s",
            getattr(resp, "status_code", "?"), full_path,
        )
        return None

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("gimie-api response was not JSON: %s", full_path)
        return None
    if not isinstance(payload, dict):
        return None
    output = payload.get("output")
    if not isinstance(output, str) or not output.strip():
        return None

    if serialization_format == "ttl":
        return output
    return _ttl_to_jsonld(output, full_path)


def _ttl_to_jsonld(ttl: str, full_path: str) -> Any:
    """Convert the sidecar's Turtle output to expanded JSON-LD, or None.

    Mirrors the in-process reference serialization
    (``json.loads(graph.serialize(format="json-ld"))``) so consumers see the
    exact shape the pipeline was built against. The sidecar's error path puts
    a plain error message in ``output``, which won't parse as Turtle → None.
    """
    graph = RDFGraph()
    try:
        graph.parse(data=ttl, format="turtle")
    except Exception:  # noqa: BLE001 — error-contract strings land here.
        logger.warning(
            "gimie-api output for %s did not parse as Turtle (likely an error): %.*s",
            full_path, _ERROR_OUTPUT_PREVIEW, ttl,
        )
        return None
    try:
        return json.loads(graph.serialize(format="json-ld"))
    except Exception:  # noqa: BLE001 — defensive: serializer failures degrade to None.
        logger.warning("gimie TTL→JSON-LD serialization failed for %s", full_path)
        return None


__all__ = ["extract_gimie_via_api", "gimie_api_base"]
