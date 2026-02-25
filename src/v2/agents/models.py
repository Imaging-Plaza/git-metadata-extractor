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
ENTITY_BUCKET_KEYS: tuple[str, ...] = (
    "repositories",
    "persons",
    "organizations",
    "articles",
    "memberships",
    "contributions",
)
ENTITY_BUCKET_ALIASES: dict[str, str] = {
    "repository": "repositories",
    "repo": "repositories",
    "repositories": "repositories",
    "softwaresourcecode": "repositories",
    "person": "persons",
    "persons": "persons",
    "organization": "organizations",
    "organizations": "organizations",
    "org": "organizations",
    "article": "articles",
    "articles": "articles",
    "publication": "articles",
    "publications": "articles",
    "scholarlyarticle": "articles",
    "membership": "memberships",
    "memberships": "memberships",
    "contribution": "contributions",
    "contributions": "contributions",
}
AGENT_BUCKET_HINTS: dict[str, str] = {
    "repo_agent": "repositories",
    "repository_agent": "repositories",
    "person_agent": "persons",
    "org_agent": "organizations",
    "organization_agent": "organizations",
    "article_agent": "articles",
    "membership_agent": "memberships",
    "contribution_agent": "contributions",
}


def normalize_entity_bucket_key(value: str | None) -> str | None:
    """Normalize entity class names and aliases to one of the six bucket keys."""
    if not isinstance(value, str):
        return None

    normalized = value.strip().lower()
    if not normalized:
        return None

    normalized = normalized.split(":")[-1]
    normalized = normalized.rsplit("/", maxsplit=1)[-1]
    return ENTITY_BUCKET_ALIASES.get(normalized)


def _first_bucket_from_type_value(value: Any) -> str | None:
    if isinstance(value, str):
        return normalize_entity_bucket_key(value)
    if isinstance(value, list):
        for item in value:
            bucket = normalize_entity_bucket_key(item if isinstance(item, str) else None)
            if bucket is not None:
                return bucket
    return None


def infer_entity_bucket(
    *,
    agent_key: str | None = None,
    data: dict[str, Any] | None = None,
) -> str | None:
    """Infer the canonical bucket key for an agent payload."""
    if isinstance(data, dict):
        for field_name in ("entity_bucket", "entity_type"):
            bucket = normalize_entity_bucket_key(data.get(field_name))
            if bucket is not None:
                return bucket

        for field_name in ("@type", "type"):
            bucket = _first_bucket_from_type_value(data.get(field_name))
            if bucket is not None:
                return bucket

    if isinstance(agent_key, str) and agent_key:
        normalized_key = agent_key.split(":", maxsplit=1)[0].strip().lower()
        hinted_bucket = AGENT_BUCKET_HINTS.get(normalized_key)
        if hinted_bucket is not None:
            return hinted_bucket

        if normalized_key.endswith("_agent"):
            return normalize_entity_bucket_key(normalized_key[: -len("_agent")])

    return None


@dataclass(slots=True)
class TypedEntityBuckets:
    """Fixed six-bucket runtime collection for pipeline entity aggregation."""

    repositories: list[dict[str, Any]] = field(default_factory=list)
    persons: list[dict[str, Any]] = field(default_factory=list)
    organizations: list[dict[str, Any]] = field(default_factory=list)
    articles: list[dict[str, Any]] = field(default_factory=list)
    memberships: list[dict[str, Any]] = field(default_factory=list)
    contributions: list[dict[str, Any]] = field(default_factory=list)

    def _bucket(self, bucket_name: str) -> list[dict[str, Any]] | None:
        normalized_name = normalize_entity_bucket_key(bucket_name)
        if normalized_name is None:
            return None
        return getattr(self, normalized_name)

    def add(self, bucket_name: str, entity: dict[str, Any]) -> None:
        bucket = self._bucket(bucket_name)
        if bucket is None or not isinstance(entity, dict) or not entity:
            return

        entity_id = entity.get("id")
        if isinstance(entity_id, str) and any(
            isinstance(existing.get("id"), str) and existing["id"] == entity_id
            for existing in bucket
        ):
            return

        bucket.append(dict(entity))

    def merge(self, other: TypedEntityBuckets) -> None:
        for bucket_name in ENTITY_BUCKET_KEYS:
            for entity in getattr(other, bucket_name):
                self.add(bucket_name, entity)

    def has_entities(self) -> bool:
        return any(getattr(self, bucket_name) for bucket_name in ENTITY_BUCKET_KEYS)

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            bucket_name: [dict(entity) for entity in getattr(self, bucket_name)]
            for bucket_name in ENTITY_BUCKET_KEYS
        }


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
    is_partial: bool = False
    failure_reason: str | None = None
    model: str | None = None
    provider: str | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    stats: dict[str, Any] = field(default_factory=dict)


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
