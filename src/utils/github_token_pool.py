"""Per-process round-robin over comma-separated GitHub PATs.

Two consumers share this helper:

- `src/v2/ingest/providers/github_provider.py` — REST calls in the v2
  pipeline, via `github_auth_headers()`.
- `src/v1/parsers/{users,orgs}_parser.py` — GraphQL + REST calls in the
  legacy parsers still invoked by the v2 hybrid runtime.

`src/api.py` startup splits a comma-separated `GME_GITHUB_TOKEN` into a
`GME_GITHUB_TOKEN_POOL` (full list) and a single `GME_GITHUB_TOKEN` (first
token). This helper prefers the pool, so v1 parsers pick up every PAT
instead of the single normalised entry.
"""

from __future__ import annotations

import itertools
import os
import threading

_LOCK = threading.Lock()
_CYCLE: itertools.cycle | None = None
_SOURCE: str | None = None


def _parse_tokens(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def next_github_token() -> str:
    """Return the next GitHub token (round-robin), or '' if none configured."""
    global _CYCLE, _SOURCE
    raw = os.environ.get("GME_GITHUB_TOKEN_POOL", "") or os.environ.get(
        "GME_GITHUB_TOKEN",
        "",
    )
    with _LOCK:
        if raw != _SOURCE or _CYCLE is None:
            tokens = _parse_tokens(raw)
            _CYCLE = itertools.cycle(tokens) if tokens else None
            _SOURCE = raw
        if _CYCLE is None:
            return ""
        return next(_CYCLE)


def github_auth_headers(
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build GitHub request headers with a rotated bearer token.

    Returns a new dict per call; never mutates `extra`. Caller-provided
    headers (including `Accept`) override the defaults — useful for the v1
    parsers which still ask for `application/vnd.github.v3+json`.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if extra:
        headers.update(extra)
    token = next_github_token()
    if token:
        headers["Authorization"] = f"token {token}"
    return headers
