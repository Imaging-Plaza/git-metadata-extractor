from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator  # type: ignore[import-untyped]

ENTITY_SCHEMA_NAME_MAP = {
    "person": "person",
    "persons": "person",
    "organization": "organization",
    "organizations": "organization",
    "repository": "repository",
    "repositories": "repository",
    "membership": "membership",
    "memberships": "membership",
    "contribution": "contribution",
    "contributions": "contribution",
    "article": "article",
    "articles": "article",
}


@dataclass(slots=True)
class ValidationResult:
    entity_type: str
    is_valid: bool
    errors: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class BatchValidationResult:
    valid_entities: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    invalid_entities: list[tuple[str, dict[str, Any], ValidationResult]] = field(
        default_factory=list,
    )
    warnings: list[str] = field(default_factory=list)


def _schema_name_for_entity(entity_type: str) -> str:
    schema_name = ENTITY_SCHEMA_NAME_MAP.get(entity_type.strip().lower())
    if schema_name is None:
        message = f"Unsupported strict schema entity type: {entity_type}"
        raise ValueError(message)
    return schema_name


def _json_error_path(path_tokens: list[Any]) -> str:
    if not path_tokens:
        return "<root>"
    return ".".join(str(token) for token in path_tokens)


@lru_cache(maxsize=8)
def _load_strict_schema(schema_name: str) -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "strict"
        / f"{schema_name}.schema.json"
    )
    with schema_path.open(encoding="utf-8") as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        raise TypeError
    return parsed


class StrictSchemaValidator:
    """Strict JSON Schema validator for reconciled v2 entities."""

    def __init__(self) -> None:
        self._validator_cache: dict[str, Draft7Validator] = {}

    def _validator_for_entity(self, entity_type: str) -> Draft7Validator:
        schema_name = _schema_name_for_entity(entity_type)
        validator = self._validator_cache.get(schema_name)
        if validator is None:
            validator = Draft7Validator(_load_strict_schema(schema_name))
            self._validator_cache[schema_name] = validator
        return validator

    @staticmethod
    def _error_payload(error: Any) -> dict[str, str]:
        return {
            "path": _json_error_path(list(error.path)),
            "message": error.message,
            "constraint": str(error.validator),
            "expected": str(error.validator_value),
        }

    def validate(self, entity_type: str, data: dict[str, Any]) -> ValidationResult:
        validator = self._validator_for_entity(entity_type)
        errors = sorted(
            validator.iter_errors(data),
            key=lambda err: ([str(token) for token in err.path], err.message),
        )
        formatted_errors = [self._error_payload(error) for error in errors]
        return ValidationResult(
            entity_type=_schema_name_for_entity(entity_type),
            is_valid=not formatted_errors,
            errors=formatted_errors,
        )

    def validate_batch(
        self,
        entities: list[tuple[str, dict[str, Any]]],
    ) -> BatchValidationResult:
        result = BatchValidationResult()

        for entity_type, payload in entities:
            validation = self.validate(entity_type, payload)
            if validation.is_valid:
                result.valid_entities.append((validation.entity_type, payload))
                continue

            result.invalid_entities.append((validation.entity_type, payload, validation))
            for error in validation.errors:
                result.warnings.append(
                    (
                        f"{validation.entity_type} at {error['path']}: "
                        f"{error['message']} (constraint={error['constraint']}, "
                        f"expected={error['expected']})"
                    ),
                )

        return result
