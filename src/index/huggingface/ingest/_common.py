"""Shared helpers for the per-entity ingest modules."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

LOGGER = logging.getLogger(__name__)


def info_to_dict(info: Any) -> dict[str, Any]:
    """Convert a huggingface_hub `ModelInfo` / `DatasetInfo` / `SpaceInfo`
    object to a plain JSON-serialisable dict.

    The SDK objects don't expose a stable `.to_dict()` across versions; we
    iterate over the object's `__dict__` and fall back to `str()` for any
    non-JSON-native value.
    """
    if info is None:
        return {}
    raw = getattr(info, "__dict__", None)
    if raw is None:
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        out[key] = _json_safe(value)
    return out


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    raw = getattr(value, "__dict__", None)
    if raw:
        return {k: _json_safe(v) for k, v in raw.items() if not k.startswith("_")}
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


def normalise_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def author_from_repo_id(repo_id: str) -> str | None:
    if "/" not in repo_id:
        return None
    return repo_id.split("/", 1)[0]


def card_data_to_dict(card_data: Any) -> dict[str, Any] | None:
    """Coerce huggingface_hub's `CardData` object into a plain dict."""
    if card_data is None:
        return None
    for attr in ("to_dict", "__dict__"):
        candidate = getattr(card_data, attr, None)
        if callable(candidate):
            try:
                return _json_safe(candidate())
            except Exception:  # noqa: BLE001 - SDK quirks; fall through
                continue
        if isinstance(candidate, dict) and candidate:
            return {k: _json_safe(v) for k, v in candidate.items() if not k.startswith("_")}
    if isinstance(card_data, dict):
        return _json_safe(card_data)
    return None


def base_models_from_card_data(card_data: dict[str, Any] | None) -> list[str] | None:
    if not card_data:
        return None
    raw = card_data.get("base_model") or card_data.get("base_models")
    if raw is None:
        return None
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(b) for b in raw if b]
    return None


def description_from_card_data(card_data: dict[str, Any] | None) -> str | None:
    if not card_data:
        return None
    for key in ("description", "summary", "short_description"):
        value = card_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
