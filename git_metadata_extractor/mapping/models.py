"""Declarative field rules; YAML cannot execute Python or import transforms."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    Field,
    JsonValue,
    field_validator,
    model_serializer,
    model_validator,
)

from git_metadata_extractor.models import DataModel


class FieldRule(DataModel, frozen=True):
    sources: list[str] | None = Field(default=None, min_length=1)
    value: JsonValue = None
    transform: Literal["identity", "iri", "web_iri", "strings", "enum"] = "identity"
    missing: Literal["omit", "null"] = "omit"
    empty: Literal["missing", "preserve"] = "missing"
    values: dict[str, JsonValue] | None = None

    @model_serializer(mode="wrap")
    def serialize_input(self, handler):
        result = handler(self)
        result.pop("value" if self.sources is not None else "sources", None)
        return result

    @model_validator(mode="after")
    def exclusive_input(self) -> FieldRule:
        if (self.sources is not None) == ("value" in self.model_fields_set):
            message = "A field rule requires exactly one of sources or value"
            raise ValueError(message)
        if (self.transform == "enum") != (self.values is not None):
            message = (
                "enum transforms require values; other transforms cannot declare values"
            )
            raise ValueError(message)
        return self

    @field_validator("sources")
    @classmethod
    def valid_paths(cls, paths: list[str] | None) -> list[str] | None:
        if paths is None:
            return paths
        if any(not all(path.split(".")) or path != path.strip() for path in paths):
            message = "Source paths must contain nonempty dot-separated keys"
            raise ValueError(message)
        return paths


class MappingSpec(DataModel, frozen=True):
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(min_length=1)
    source_contract: str = Field(min_length=1)
    target_contract: str = Field(pattern=r"^(agent|raw|canonical)\.[a-z][a-z0-9_]*$")
    fields: dict[str, FieldRule] = Field(default_factory=dict)
    internal_fields: dict[str, FieldRule] = Field(default_factory=dict)

    @model_validator(mode="after")
    def internal_boundary(self) -> MappingSpec:
        keys = list(self.fields) + list(self.internal_fields)
        for key in keys:
            if not all(key.split(".")):
                message = "Target paths must contain nonempty keys"
                raise ValueError(message)
            if any(other != key and other.startswith(key + ".") for other in keys):
                message = f"Target paths collide at {key}"
                raise ValueError(message)
        if any(key.startswith("_") for key in self.fields):
            message = "Internal targets belong in internal_fields"
            raise ValueError(message)
        if self.internal_fields and (
            not self.target_contract.startswith("agent.")
            or any(not key.startswith("_") for key in self.internal_fields)
        ):
            message = (
                "Only agent mappings may declare underscore-prefixed internal fields"
            )
            raise ValueError(message)
        return self
