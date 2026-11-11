"""Small, explicit transformation registry. Add transformations here, not in YAML."""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import JsonValue


def identity(value: JsonValue) -> JsonValue:
    return deepcopy(value)


def iri(value: JsonValue) -> JsonValue:
    return {"@id": value} if isinstance(value, str) else None


def strings(value: JsonValue) -> JsonValue:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str) and item]


TRANSFORMS = MappingProxyType({"identity": identity, "iri": iri, "strings": strings})
