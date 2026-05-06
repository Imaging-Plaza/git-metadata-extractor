"""Project Renku JSON payloads to flat DuckDB rows.

The Renku data API uses snake_case for resource endpoints (`/projects`,
`/data_connectors`) and camelCase for the `/search/query` index. Both
shapes are normalised here.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)")


def _parse_timestamp(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    if not isinstance(raw, str):
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        m = _TIMESTAMP_RE.match(s)
        if not m:
            return None
        try:
            return datetime.fromisoformat(m.group(1))
        except ValueError:
            return None


def _namespace_path(item: dict[str, Any]) -> str | None:
    ns = item.get("namespace")
    if isinstance(ns, str):
        return ns
    if isinstance(ns, dict):
        return ns.get("path") or ns.get("slug")
    return None


def project_project(item: dict[str, Any]) -> dict[str, Any] | None:
    project_id = item.get("id")
    if not project_id:
        return None
    return {
        "project_id": str(project_id),
        "slug": item.get("slug"),
        "name": item.get("name"),
        "namespace": _namespace_path(item),
        "path": item.get("path"),
        "description": item.get("description") or None,
        "visibility": item.get("visibility"),
        "is_template": item.get("is_template"),
        "keywords": item.get("keywords") or [],
        "repositories": item.get("repositories") or [],
        "created_by": _created_by_id(item.get("created_by") or item.get("createdBy")),
        "creation_date": _parse_timestamp(
            item.get("creation_date") or item.get("creationDate"),
        ),
        "updated_at": _parse_timestamp(item.get("updated_at") or item.get("updatedAt")),
    }


def project_group(item: dict[str, Any]) -> dict[str, Any] | None:
    group_id = item.get("id")
    if not group_id:
        return None
    return {
        "group_id": str(group_id),
        "slug": item.get("slug"),
        "name": item.get("name"),
        "description": item.get("description") or None,
        "created_by": _created_by_id(item.get("created_by") or item.get("createdBy")),
        "creation_date": _parse_timestamp(
            item.get("creation_date") or item.get("creationDate"),
        ),
    }


def project_user(item: dict[str, Any]) -> dict[str, Any] | None:
    user_id = item.get("id")
    if not user_id:
        return None
    return {
        "user_id": str(user_id),
        "slug": item.get("slug"),
        "path": item.get("path"),
        "first_name": item.get("first_name") or item.get("firstName"),
        "last_name": item.get("last_name") or item.get("lastName"),
    }


def project_data_connector(item: dict[str, Any]) -> dict[str, Any] | None:
    dc_id = item.get("id")
    if not dc_id:
        return None

    storage = item.get("storage") or {}
    storage_type = (
        storage.get("storage_type")
        or storage.get("storageType")
        or item.get("storageType")
    )
    config_block: dict[str, Any] = {}
    raw_cfg = storage.get("configuration")
    if isinstance(raw_cfg, dict):
        config_block = raw_cfg
    storage_provider = config_block.get("provider") or item.get("storageProvider")
    source_path = storage.get("source_path") or storage.get("sourcePath")
    target_path = storage.get("target_path") or storage.get("targetPath")
    readonly = storage.get("readonly")
    if readonly is None:
        readonly = item.get("readonly")

    return {
        "data_connector_id": str(dc_id),
        "slug": item.get("slug"),
        "name": item.get("name"),
        "namespace": _namespace_path(item),
        "path": item.get("path"),
        "description": item.get("description") or None,
        "visibility": item.get("visibility"),
        "storage_type": storage_type,
        "storage_provider": storage_provider,
        "source_path": source_path,
        "target_path": target_path,
        "readonly": readonly,
        "keywords": item.get("keywords") or [],
        "created_by": _created_by_id(item.get("created_by") or item.get("createdBy")),
        "creation_date": _parse_timestamp(
            item.get("creation_date") or item.get("creationDate"),
        ),
    }


def project_member(item: dict[str, Any]) -> dict[str, Any] | None:
    user_id = item.get("id") or item.get("user_id") or item.get("userId")
    if not user_id:
        return None
    return {
        "user_id": str(user_id),
        "first_name": item.get("first_name") or item.get("firstName"),
        "last_name": item.get("last_name") or item.get("lastName"),
        "path": item.get("namespace") or item.get("path"),
        "slug": item.get("namespace") or item.get("slug"),
        "role": item.get("role"),
    }


def _created_by_id(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("id") or raw.get("user_id") or raw.get("userId")
    return None
