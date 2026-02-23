from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jsonschema import Draft7Validator  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from src.v2.providers.base import (
        GitHubProvider,
        InfoscienceProvider,
        ORCIDProvider,
        RORProvider,
    )

MAX_PERMISSIVE_PASSES = 5


@dataclass(slots=True, frozen=True)
class ProviderSet:
    """Dependency injection bundle for v2 agents."""

    github: GitHubProvider
    orcid: ORCIDProvider | None = None
    infoscience: InfoscienceProvider | None = None
    ror: RORProvider | None = None


@dataclass(slots=True)
class AgentResult:
    """Normalized result contract for v2 agent wrappers."""

    data: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    raw_output: dict[str, Any] = field(default_factory=dict)


def _warn_once(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _json_path(path_tokens: list[Any]) -> str:
    if not path_tokens:
        return "<root>"
    return ".".join(str(token) for token in path_tokens)


def _top_level_field(path_tokens: list[Any]) -> str | None:
    if not path_tokens:
        return None
    top = path_tokens[0]
    if isinstance(top, str):
        return top
    return None


@lru_cache(maxsize=8)
def load_agent_schema(schema_name: str) -> dict[str, Any]:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "agent"
        / f"{schema_name}.schema.json"
    )
    with schema_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise TypeError
    return payload


def validate_permissive(
    payload: dict[str, Any],
    *,
    schema_name: str,
) -> tuple[dict[str, Any], list[str]]:
    """Validate against agent schema while soft-dropping invalid optional fields."""
    schema = load_agent_schema(schema_name)
    validator = Draft7Validator(schema)
    required_fields = {
        field_name
        for field_name in schema.get("required", [])
        if isinstance(field_name, str)
    }

    validated_payload = deepcopy(payload)
    warnings: list[str] = []

    for _ in range(MAX_PERMISSIVE_PASSES):
        errors = sorted(validator.iter_errors(validated_payload), key=lambda err: list(err.path))
        if not errors:
            return validated_payload, warnings

        changed = False
        for error in errors:
            error_path = list(error.path)
            field_name = _top_level_field(error_path)
            path_text = _json_path(error_path)

            if error.validator == "additionalProperties" and isinstance(error.instance, dict):
                properties = schema.get("properties", {})
                valid_keys = (
                    {key for key in properties if isinstance(key, str)}
                    if isinstance(properties, dict)
                    else set()
                )
                unexpected = sorted(
                    key for key in error.instance if key not in valid_keys
                )
                if unexpected:
                    for extra_field in unexpected:
                        validated_payload.pop(extra_field, None)
                    _warn_once(
                        warnings,
                        f"Removed unexpected fields: {', '.join(unexpected)}",
                    )
                    changed = True
                continue

            if (
                field_name
                and field_name not in required_fields
                and field_name in validated_payload
            ):
                validated_payload.pop(field_name, None)
                _warn_once(
                    warnings,
                    f"Removed invalid optional field '{field_name}' at {path_text}: {error.message}",
                )
                changed = True
                continue

            _warn_once(
                warnings,
                f"Validation warning at {path_text}: {error.message}",
            )

        if not changed:
            break

    for error in sorted(validator.iter_errors(validated_payload), key=lambda err: list(err.path)):
        _warn_once(
            warnings,
            f"Validation warning at {_json_path(list(error.path))}: {error.message}",
        )

    return validated_payload, warnings
