from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

AUTHORITATIVE_IDENTIFIER_KEYS: dict[str, set[str]] = {
    "person": {"orcid", "pulse:orcid", "pulse:orcidIdentifier"},
    "organization": {"ror", "pulse:ror", "schema:identifier"},
    "repository": {"doi", "schema:identifier", "pulse:githubRepositoryHandle"},
}
IDENTIFIER_PATH_DEPTH = 2


@dataclass(frozen=True, slots=True)
class MergeResult:
    merged_data: dict[str, Any]
    changed_fields: list[str]
    provenance_updates: list[dict[str, Any]]


class MergePolicy:
    def __init__(
        self,
        *,
        source: str = "unknown",
        run_id: str | None = None,
    ) -> None:
        self._source = source
        self._run_id = run_id

    def merge_entities(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
        entity_type: str,
    ) -> MergeResult:
        merged_data, changes = self._merge_dict(
            existing=existing,
            incoming=incoming,
            path=(),
            entity_type=entity_type,
        )
        changed_fields = [change["field"] for change in changes]
        timestamp = datetime.now(timezone.utc).isoformat()
        provenance_updates = [
            {
                "field": change["field"],
                "old_value": change["old_value"],
                "new_value": change["new_value"],
                "source": self._source,
                "run_id": self._run_id,
                "timestamp": timestamp,
            }
            for change in changes
        ]
        return MergeResult(
            merged_data=merged_data,
            changed_fields=changed_fields,
            provenance_updates=provenance_updates,
        )

    def _merge_dict(
        self,
        *,
        existing: dict[str, Any],
        incoming: dict[str, Any],
        path: tuple[str, ...],
        entity_type: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        merged: dict[str, Any] = {}
        changes: list[dict[str, Any]] = []

        for key, existing_value in existing.items():
            if key not in incoming:
                merged[key] = copy.deepcopy(existing_value)

        for key, incoming_value in incoming.items():
            key_path = (*path, key)
            if key not in existing:
                merged[key] = copy.deepcopy(incoming_value)
                if not _is_empty(incoming_value):
                    changes.append(
                        {
                            "field": _format_field_path(key_path),
                            "old_value": None,
                            "new_value": copy.deepcopy(incoming_value),
                        },
                    )
                continue

            existing_value = existing[key]
            merged_value, value_changes = self._merge_value(
                existing_value=existing_value,
                incoming_value=incoming_value,
                path=key_path,
                entity_type=entity_type,
            )
            merged[key] = merged_value
            changes.extend(value_changes)

        return merged, changes

    def _merge_value(
        self,
        *,
        existing_value: Any,
        incoming_value: Any,
        path: tuple[str, ...],
        entity_type: str,
    ) -> tuple[Any, list[dict[str, Any]]]:
        if _is_authoritative_identifier(path, entity_type) and not _is_empty(existing_value):
            return copy.deepcopy(existing_value), []

        if isinstance(existing_value, dict) and isinstance(incoming_value, dict):
            return self._merge_dict(
                existing=existing_value,
                incoming=incoming_value,
                path=path,
                entity_type=entity_type,
            )

        if isinstance(existing_value, list) and isinstance(incoming_value, list):
            merged = _dedupe_union(existing_value, incoming_value)
            if _stable_json(existing_value) == _stable_json(merged):
                return copy.deepcopy(existing_value), []
            return merged, [
                {
                    "field": _format_field_path(path),
                    "old_value": copy.deepcopy(existing_value),
                    "new_value": copy.deepcopy(merged),
                },
            ]

        preferred_scalar = _choose_scalar(existing_value, incoming_value)
        if _stable_json(preferred_scalar) == _stable_json(existing_value):
            return copy.deepcopy(existing_value), []

        return copy.deepcopy(preferred_scalar), [
            {
                "field": _format_field_path(path),
                "old_value": copy.deepcopy(existing_value),
                "new_value": copy.deepcopy(preferred_scalar),
            },
        ]


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, set, tuple)):
        return len(value) == 0
    return False


def _is_authoritative_identifier(path: tuple[str, ...], entity_type: str) -> bool:
    if len(path) != IDENTIFIER_PATH_DEPTH:
        return False
    if path[0] != "identifiers":
        return False
    keys = AUTHORITATIVE_IDENTIFIER_KEYS.get(entity_type, set())
    return path[1] in keys


def _dedupe_union(existing: list[Any], incoming: list[Any]) -> list[Any]:
    combined = [*existing, *incoming]
    deduped: list[Any] = []
    seen: set[str] = set()
    for item in combined:
        marker = _stable_json(item)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(copy.deepcopy(item))
    return deduped


def _choose_scalar(existing: Any, incoming: Any) -> Any:
    if _is_empty(incoming):
        return existing
    if _is_empty(existing):
        return incoming
    if _stable_json(existing) == _stable_json(incoming):
        return existing

    if isinstance(existing, str) and isinstance(incoming, str):
        if len(incoming.strip()) > len(existing.strip()):
            return incoming
        return existing

    return existing


def _format_field_path(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return repr(value)
