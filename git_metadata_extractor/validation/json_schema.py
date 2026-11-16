"""Shared JSON Schema construction with mandatory format support."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft7Validator


def schema_validator(schema: dict[str, Any]) -> Draft7Validator:
    checker = Draft7Validator.FORMAT_CHECKER
    if "uri" not in checker.checkers:
        message = "URI validation is unavailable; install jsonschema[format-nongpl]"
        raise RuntimeError(message)
    return Draft7Validator(schema, format_checker=checker)
