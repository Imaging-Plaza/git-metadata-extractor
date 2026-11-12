from __future__ import annotations

import json
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SkipValidation

from git_metadata_extractor.models import InternalModel
from git_metadata_extractor.providers.base import (
    GitHubProvider,
    InfoscienceProvider,
    ORCIDProvider,
    RORProvider,
)
from git_metadata_extractor.providers.sources_service import SourcesClient
from git_metadata_extractor.validation.json_schema import schema_validator
from git_metadata_extractor.validation.metadata import (
    restore_internal_metadata,
    strip_internal_metadata,
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


def generate_uuid() -> str:
    """Generate a UUIDv4 string for agent payload identifiers."""
    return str(uuid.uuid4())


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


class TypedEntityBuckets(InternalModel):
    """Fixed six-bucket runtime collection for pipeline entity aggregation."""

    repositories: list[dict[str, Any]] = Field(default_factory=list)
    persons: list[dict[str, Any]] = Field(default_factory=list)
    organizations: list[dict[str, Any]] = Field(default_factory=list)
    articles: list[dict[str, Any]] = Field(default_factory=list)
    memberships: list[dict[str, Any]] = Field(default_factory=list)
    contributions: list[dict[str, Any]] = Field(default_factory=list)

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
        return self.model_dump()


class ProviderSet(InternalModel, frozen=True):
    """Dependency injection bundle for v2 agents."""

    # Clients are injected runtime objects, including observation proxies and
    # duck-typed adapters. Validate data records, not the clients themselves.
    github: SkipValidation[GitHubProvider]
    orcid: SkipValidation[ORCIDProvider | None] = None
    infoscience: SkipValidation[InfoscienceProvider | None] = None
    ror: SkipValidation[RORProvider | None] = None
    package_registry: Any | None = None
    infoscience_rag: Any | None = None
    ethz_research_collection_rag: Any | None = None
    huggingface_rag: Any | None = None
    openalex_rag: Any | None = None
    zenodo_rag: Any | None = None
    oamonitor_rag: Any | None = None
    orcid_rag: Any | None = None
    ror_rag: Any | None = None
    snsf_rag: Any | None = None
    swissubase_rag: Any | None = None
    renkulab_rag: Any | None = None
    github_rag: Any | None = None
    epfl_graph_rag: Any | None = None
    federated_rag: Any | None = None
    sources: SkipValidation[SourcesClient | None] = None

    def close(self) -> None:
        """Legacy sync cleanup for primary clients explicitly owned by the caller."""
        from git_metadata_extractor.providers.runtime import close_resources_sync
        close_resources_sync((self.github, self.orcid, self.infoscience, self.ror))

    async def aclose(self) -> None:
        """Close a wholly caller-owned bundle, including asynchronous providers."""
        from git_metadata_extractor.providers.runtime import close_resources
        await close_resources(getattr(self, name) for name in type(self).model_fields)


class AgentResult(InternalModel):
    """Normalized result contract for v2 agent wrappers."""

    data: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    raw_output: dict[str, Any] = Field(default_factory=dict)
    is_partial: bool = False
    failure_reason: str | None = None
    model: str | None = None
    provider: str | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


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
        / "schema"
        / "json"
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
    validator = schema_validator(schema)
    required_fields = {
        field_name
        for field_name in schema.get("required", [])
        if isinstance(field_name, str)
    }

    validated_payload = strip_internal_metadata(payload)
    warnings: list[str] = []

    # `_`-prefixed keys are internal pipeline metadata (per the convention
    # documented in CLAUDE.md): stripped before strict validation, JSON-LD
    # output, and RDF serialisation. They don't need to appear in the agent
    # JSON Schema — pull them aside before running the schema validator so
    # they don't trip `additionalProperties`, then restore them at the end.
    for _ in range(MAX_PERMISSIVE_PASSES):
        errors = sorted(validator.iter_errors(validated_payload), key=lambda err: list(err.path))
        if not errors:
            restore_internal_metadata(payload, validated_payload)
            return validated_payload, warnings

        changed = False
        for error in errors:
            error_path = list(error.path)
            field_name = _top_level_field(error_path)
            path_text = _json_path(error_path)

            if error.validator == "additionalProperties" and isinstance(error.instance, dict):
                properties = error.schema.get("properties", {})
                valid_keys = (
                    {key for key in properties if isinstance(key, str)}
                    if isinstance(properties, dict)
                    else set()
                )
                unexpected = sorted(
                    key for key in error.instance if key not in valid_keys
                    and not any(re.search(pattern, key) for pattern in error.schema.get("patternProperties", {}))
                )
                if unexpected:
                    for extra_field in unexpected:
                        error.instance.pop(extra_field, None)
                    _warn_once(
                        warnings,
                        f"Removed unexpected fields at {path_text}: {', '.join(unexpected)}",
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

    # Re-attach internal metadata pulled aside before the schema check.
    restore_internal_metadata(payload, validated_payload)

    return validated_payload, warnings
