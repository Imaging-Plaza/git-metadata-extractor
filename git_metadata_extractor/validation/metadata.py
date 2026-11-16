"""Keep pipeline metadata out of public data at every nesting depth."""

from copy import deepcopy
from typing import TypeVar, cast

T = TypeVar("T")


def strip_internal_metadata(value: T) -> T:
    """Copy a payload without `_`-prefixed dictionary keys, including in lists."""
    if isinstance(value, dict):
        return cast(
            "T",
            {
                key: strip_internal_metadata(child)
                for key, child in value.items()
                if not (isinstance(key, str) and key.startswith("_"))
            },
        )
    if isinstance(value, list):
        return cast("T", [strip_internal_metadata(child) for child in value])
    return deepcopy(value)


def restore_internal_metadata(original: object, validated: object) -> None:
    """Reattach metadata to surviving agent fields; never restore rejected data."""
    if isinstance(original, dict) and isinstance(validated, dict):
        for key, value in original.items():
            if isinstance(key, str) and key.startswith("_"):
                validated[key] = deepcopy(value)
            elif key in validated:
                restore_internal_metadata(value, validated[key])
    elif isinstance(original, list) and isinstance(validated, list):
        for before, after in zip(original, validated):
            restore_internal_metadata(before, after)
