"""HTTP client for the ``gimie-api`` sidecar (``ghcr.io/sdsc-ordes/gimie-api``).

A drop-in replacement for the in-process ``extract_gimie`` that calls a sidecar
service running the same gimie version, so the heavy ``gimie`` (+ ``calamus``)
dependency can eventually leave this project's Python tree. Enabled by setting
``GIMIE_API_URL`` (e.g. ``http://gme-gimie-api:15400``); when unset, callers fall
back to in-process gimie.

The sidecar's ``GET /gimie/jsonld/{full_path}`` returns
``{"link": <url>, "output": "<json-ld string>"}``. NOTE its error contract:
failures come back as **HTTP 200** with ``output`` set to the error *message*
(not valid JSON), so we validate that ``output`` parses to JSON-LD and return
``None`` otherwise — matching the in-process extractor's degrade-to-None
behaviour.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

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
    """Fetch a repo's gimie JSON-LD from the gimie-api sidecar.

    Signature-compatible with ``src.v1.gimie_utils.gimie_methods.extract_gimie``:
    returns the parsed JSON-LD (``dict`` with ``@graph`` / ``@context``) for
    ``serialization_format="json-ld"``, the TTL string for ``"ttl"``, or ``None``
    on any failure (sidecar down, non-200, the HTTP-200 error contract, or a
    response that doesn't parse to JSON-LD).
    """
    base = gimie_api_base()
    if base is None:
        return None
    http = session if session is not None else requests
    kind = "ttl" if serialization_format == "ttl" else "jsonld"
    url = f"{base}/gimie/{kind}/{full_path}"

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

    if serialization_format == "ttl":
        return output if isinstance(output, str) and output.strip() else None
    return _parse_jsonld_output(output, full_path)


def _parse_jsonld_output(output: Any, full_path: str) -> Any:
    """Coerce the sidecar's ``output`` into parsed JSON-LD, or None.

    ``output`` is normally the serialized JSON-LD *string*; we defensively accept
    an already-parsed dict/list too. The sidecar's error path puts a plain error
    message here, which won't parse → None.
    """
    if isinstance(output, (dict, list)):
        return output
    if not isinstance(output, str) or not output.strip():
        return None
    try:
        parsed = json.loads(output)
    except ValueError:
        logger.warning(
            "gimie-api returned a non-JSON output for %s (likely an error): %.*s",
            full_path, _ERROR_OUTPUT_PREVIEW, output,
        )
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


__all__ = ["extract_gimie_via_api", "gimie_api_base"]
