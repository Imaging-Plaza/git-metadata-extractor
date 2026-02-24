from __future__ import annotations

from src.v2.testing.provider_snapshot_sanitizer import (
    contains_secret_like_text,
    redact_token_like_text,
    sanitize_headers,
    sanitize_json_payload,
    sanitize_snapshot_metadata,
)


def test_sanitize_headers_keeps_operational_and_drops_sensitive() -> None:
    headers = {
        "Authorization": "Bearer ghp_abcdefghijklmnopqrstuvwxyz123456",
        "X-RateLimit-Remaining": "4999",
        "ETag": '"abc"',
        "X-Request-Id": "request-123",
        "Set-Cookie": "session=xyz",
    }

    sanitized = sanitize_headers(headers)

    assert "authorization" not in sanitized
    assert "set-cookie" not in sanitized
    assert "x-request-id" not in sanitized
    assert sanitized["x-ratelimit-remaining"] == "4999"
    assert sanitized["etag"] == '"abc"'


def test_sanitize_json_payload_removes_sensitive_keys_and_redacts_values() -> None:
    payload = {
        "name": "example",
        "access_token": "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "nested": {
            "Authorization": "Bearer ghp_abcdefghijklmnopqrstuvwxyz123456",
            "note": "token=ghp_abcdefghijklmnopqrstuvwxyz123456",
        },
    }

    sanitized = sanitize_json_payload(payload)

    assert "access_token" not in sanitized
    assert "Authorization" not in sanitized["nested"]
    assert sanitized["nested"]["note"].count("<redacted>") == 1


def test_sanitize_snapshot_metadata_applies_header_rules() -> None:
    metadata = {
        "request": {
            "headers": {
                "Authorization": "Bearer ghp_abcdefghijklmnopqrstuvwxyz123456",
                "Content-Type": "application/json",
            },
        },
        "response": {
            "headers": {
                "X-RateLimit-Remaining": "12",
                "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
                "X-Request-Id": "private-request",
            },
        },
    }

    sanitized = sanitize_snapshot_metadata(metadata)

    request_headers = sanitized["request"]["headers"]
    response_headers = sanitized["response"]["headers"]

    assert request_headers == {"content-type": "application/json"}
    assert response_headers["x-ratelimit-remaining"] == "12"
    assert response_headers["date"] == "Mon, 01 Jan 2024 00:00:00 GMT"
    assert "x-request-id" not in response_headers


def test_secret_detection_and_redaction_helpers() -> None:
    secret_text = "Authorization: Bearer " + "ghp_abcdefghijklmnopqrstuvwxyz123456"

    assert contains_secret_like_text(secret_text)
    redacted = redact_token_like_text(secret_text)
    assert "ghp_" not in redacted
    assert "<redacted>" in redacted
