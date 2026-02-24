from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_KEYWORDS = (
    "authorization",
    "cookie",
    "set-cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "api-key",
    "apikey",
    "private-key",
    "session",
)

SAFE_RESPONSE_HEADERS = {
    "cache-control",
    "content-type",
    "date",
    "etag",
    "last-modified",
    "link",
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-ratelimit-resource",
    "x-ratelimit-used",
}

TOKEN_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]{10,}\.[A-Za-z0-9._-]{10,}"),
)

REDACTED_VALUE = "<redacted>"


def _normalize_key(key: str) -> str:
    return key.strip().lower()


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(keyword in normalized for keyword in SENSITIVE_KEYWORDS)


def redact_token_like_text(value: str) -> str:
    sanitized = value
    for pattern in TOKEN_PATTERNS:
        sanitized = pattern.sub(REDACTED_VALUE, sanitized)
    return sanitized


def contains_secret_like_text(value: str) -> bool:
    return any(pattern.search(value) for pattern in TOKEN_PATTERNS)


def sanitize_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str):
            continue

        normalized_key = _normalize_key(key)
        if _is_sensitive_key(normalized_key):
            continue

        is_operational = (
            normalized_key in SAFE_RESPONSE_HEADERS
            or normalized_key.startswith("x-ratelimit-")
        )
        if not is_operational:
            continue

        if isinstance(value, str):
            sanitized[normalized_key] = redact_token_like_text(value)
        else:
            sanitized[normalized_key] = str(value)

    return sanitized


def sanitize_json_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized_mapping: dict[str, Any] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                continue
            if _is_sensitive_key(key):
                continue
            sanitized_mapping[key] = sanitize_json_payload(nested_value)
        return sanitized_mapping

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_json_payload(item) for item in value]

    if isinstance(value, str):
        return redact_token_like_text(value)

    return value


def sanitize_snapshot_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_json_payload(dict(metadata))
    if not isinstance(sanitized, dict):
        return {}

    request_payload = sanitized.get("request")
    if isinstance(request_payload, dict):
        request_headers = request_payload.get("headers")
        if isinstance(request_headers, Mapping):
            request_payload["headers"] = sanitize_headers(request_headers)

    response_payload = sanitized.get("response")
    if isinstance(response_payload, dict):
        response_headers = response_payload.get("headers")
        if isinstance(response_headers, Mapping):
            response_payload["headers"] = sanitize_headers(response_headers)

    return sanitized
