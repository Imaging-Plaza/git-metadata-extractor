"""Shared helpers for OAM-CH per-entity ingest projectors."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_OA_COLOR_LABELS: dict[int, str] = {
    0: "closed",
    1: "gold",
    2: "green",
    3: "hybrid",
    4: "bronze",
    5: "diamond",
    6: "subscribe-to-open",
    7: "transformative",
}


def oa_color_label(value: Any) -> str | None:
    """Return the textual label for an OAM ``oa_color`` enum value."""
    if not isinstance(value, int):
        return None
    return _OA_COLOR_LABELS.get(value)


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse OAM's ISO 8601 ``updated`` field. Returns ``None`` on miss."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    # Normalise the trailing ``Z`` so ``fromisoformat`` accepts it on 3.10+.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _coalesce_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coalesce_list_of_str(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _label_strings(labels: Any) -> list[str]:
    """Extract free-text labels from an OAM ``labels`` list of ``{label, iso639}``."""
    out: list[str] = []
    if not isinstance(labels, list):
        return out
    for entry in labels:
        if isinstance(entry, dict):
            value = entry.get("label")
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
    return out


__all__ = [
    "_coalesce_list_of_str",
    "_coalesce_str",
    "_label_strings",
    "oa_color_label",
    "parse_iso_datetime",
]
