"""Small, explicit transformation registry. Add transformations here, not in YAML."""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import AnyHttpUrl, ValidationError

if TYPE_CHECKING:
    from pydantic import JsonValue


def identity(value: JsonValue) -> JsonValue:
    return deepcopy(value)


def iri(value: JsonValue) -> JsonValue:
    return {"@id": value} if isinstance(value, str) else None


def web_iri(value: JsonValue) -> JsonValue:
    """Validate web links without resolving bare domains against the RDF base."""
    if not isinstance(value, str) or not value.lower().startswith(("http://", "https://")):
        return None
    try:
        return {"@id": str(AnyHttpUrl(value))}
    except ValidationError:
        return None


def strings(value: JsonValue) -> JsonValue:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str) and item]


TRANSFORMS = MappingProxyType({
    "identity": identity, "iri": iri, "web_iri": web_iri, "strings": strings,
})
