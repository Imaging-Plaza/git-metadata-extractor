"""Lightweight, dependency-free request rate limiting (audit: no-rate-limit DoS).

A fixed-window, per-client limiter applied to the compute-/cost-heavy routes
(extraction + index ingest) so an abusive caller can't exhaust the LLM budget,
GitHub token, or worker pool.

Design notes / limitations:
- **Opt-in:** disabled unless ``V2_RATE_LIMIT_PER_MINUTE`` is set to a positive
  integer, so existing high-throughput batch clients are not broken by default.
- **Per-worker, in-memory:** state lives in this process. Under gunicorn with N
  workers the effective limit is ~N times the configured value, and it resets on
  restart. For a global limit use a shared store (Redis) — out of scope here.
- Keyed by bearer token (hashed) when present, else client IP.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response

_WINDOW_SECONDS = 60.0
_MAX_TRACKED_KEYS = 100_000  # bound the per-worker key table

_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)


def _configured_limit() -> int:
    raw = os.getenv("V2_RATE_LIMIT_PER_MINUTE")
    if not raw or not raw.strip():
        return 0  # disabled
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return 0


def _is_rate_limited_route(method: str, path: str) -> bool:
    """The expensive, cost-amplifying routes worth protecting."""
    if method == "POST" and (
        path == "/v2/extract"
        or (path.startswith("/v2/indices/") and path.endswith("/ingest"))
    ):
        return True
    return method == "GET" and path.startswith(
        ("/v2/extract/", "/v1/org/", "/v1/user/", "/v1/repository/"),
    )


def _client_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth:
        return "tok:" + hashlib.sha256(auth.encode("utf-8")).hexdigest()[:16]
    client = request.client
    return "ip:" + (client.host if client else "unknown")


def reset_rate_limit_state() -> None:
    """Clear the limiter window table (tests / manual reset)."""
    with _lock:
        _hits.clear()


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    limit = _configured_limit()
    if limit > 0 and _is_rate_limited_route(request.method, request.url.path):
        key = _client_key(request)
        now = time.monotonic()
        with _lock:
            if len(_hits) > _MAX_TRACKED_KEYS:
                _hits.clear()  # crude overflow guard for a per-worker table
            window = _hits[key]
            cutoff = now - _WINDOW_SECONDS
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= limit:
                retry_after = max(1, int(_WINDOW_SECONDS - (now - window[0])))
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Retry later."},
                    headers={"Retry-After": str(retry_after)},
                )
            window.append(now)
    return await call_next(request)
