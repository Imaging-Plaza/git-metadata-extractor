"""Adapter wrapping `src.index.renkulab` for federated search/lookup.

The RenkuLab index has four entity types — projects, groups, users,
data_connectors — each in its own Qdrant collection. The adapter
exposes them under a single index name `renkulab` and uses the
``entity_type`` argument to dispatch to a single collection. When
``entity_type`` is ``None`` the adapter searches across all four.
"""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_RENKU_PROJECT_URL = re.compile(
    r"https?://renkulab\.io/(?:v2/)?projects/([\w.-]+(?:/[\w.-]+)+)",
    re.IGNORECASE,
)
_RE_RENKU_GROUP_URL = re.compile(
    r"https?://renkulab\.io/(?:v2/)?groups/([\w.-]+)",
    re.IGNORECASE,
)
_RE_RENKU_USER_URL = re.compile(
    r"https?://renkulab\.io/(?:v2/)?users/([\w.-]+)",
    re.IGNORECASE,
)
_RE_ULID = re.compile(r"^[0-9A-HJ-KM-NP-TV-Z]{26}$", re.IGNORECASE)
_RE_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


_ENTITY_NAMES = ("projects", "groups", "users", "data_connectors")

_RENKU_BASE = "https://renkulab.io/v2"


def _entity_url(entity_type: str, payload: dict[str, Any]) -> str | None:
    # Projects and data connectors are namespaced; groups + users are
    # top-level slugs. When `path` (the full namespace/slug composite) is
    # unset (e.g. row came from /projects rather than /search), build it
    # from namespace + slug.
    path = payload.get("path")
    slug = payload.get("slug")
    namespace = payload.get("namespace")
    if entity_type in {"projects", "data_connectors"}:
        composite = path or (f"{namespace}/{slug}" if namespace and slug else slug)
        if not composite:
            return None
        if entity_type == "projects":
            return f"{_RENKU_BASE}/projects/{composite}"
        return f"{_RENKU_BASE}/data-connectors/{composite}"
    if entity_type == "groups" and slug:
        return f"{_RENKU_BASE}/groups/{slug}"
    if entity_type == "users":
        identifier = path or slug
        if identifier:
            return f"{_RENKU_BASE}/users/{identifier}"
    return None


def _hit_title(entity_type: str, payload: dict[str, Any]) -> str | None:
    if entity_type == "users":
        first = payload.get("first_name") or ""
        last = payload.get("last_name") or ""
        full = f"{first} {last}".strip()
        return full or payload.get("path") or payload.get("slug")
    return (
        payload.get("name")
        or payload.get("path")
        or payload.get("slug")
    )


def _summary(entity_type: str, payload: dict[str, Any]) -> str | None:
    parts: list[str] = []
    if entity_type in {"projects", "data_connectors"}:
        ns = payload.get("namespace") or payload.get("path")
        if ns:
            parts.append(str(ns))
    if entity_type == "data_connectors":
        st = payload.get("storage_type")
        if st:
            parts.append(f"storage:{st}")
    if entity_type == "users":
        path = payload.get("path") or payload.get("slug")
        if path:
            parts.append(str(path))
    vis = payload.get("visibility")
    if vis:
        parts.append(str(vis))
    return " — ".join(parts) if parts else None


class RenkulabAdapter:
    name = "renkulab"
    entity_types: list[str] = list(_ENTITY_NAMES)  # noqa: RUF012

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        from src.index.renkulab.config import load_config
        from src.index.renkulab.retrieval.semantic import semantic_search

        target_types = [entity_type] if entity_type else None
        if target_types and target_types[0] not in _ENTITY_NAMES:
            return []

        config = load_config()
        try:
            results = semantic_search(
                config=config, query=query,
                entity_types=target_types,
                top_k=top_k, candidate_k=max(top_k * 5, 50),
                filter_payload=filters,
            )
        except Exception:  # noqa: BLE001
            return []

        out: list[Hit] = []
        for r in results:
            payload = r.get("payload") or {}
            et = str(payload.get("entity_type") or "")
            entity_id = payload.get("entity_id") or r.get("id")
            if not entity_id or not et:
                continue
            out.append(Hit(
                index=self.name,
                entity_type=et,
                id=str(entity_id),
                title=_hit_title(et, payload),
                score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                summary=_summary(et, payload),
                url=_entity_url(et, payload),
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        if not s:
            return []

        # URL-based dispatch first.
        for pattern, et in (
            (_RE_RENKU_PROJECT_URL, "projects"),
            (_RE_RENKU_GROUP_URL, "groups"),
            (_RE_RENKU_USER_URL, "users"),
        ):
            m = pattern.search(s)
            if m:
                return self._lookup_by_path_or_id(et, m.group(1))

        # Bare ULID / UUID — try each entity type until one resolves.
        if _RE_ULID.match(s) or _RE_UUID.match(s):
            from src.index.renkulab.storage.duckdb_store import RenkulabStore

            store = RenkulabStore.open()
            try:
                for et in _ENTITY_NAMES:
                    try:
                        row = store.fetch_entity(et, s)
                    except Exception:  # noqa: BLE001
                        row = None
                    if row is not None:
                        return [self._record_from_row(et, str(s), row)]
            finally:
                store.close()
            return []

        # Otherwise interpret as a slug for any entity.
        return self._lookup_by_slug_anywhere(s)

    def _lookup_by_path_or_id(
        self,
        entity_type: str,
        identifier: str,
    ) -> list[EntityRecord]:
        from src.index.renkulab.retrieval.sql import run_adhoc

        table = _ENTITY_TABLE[entity_type]

        # Only `projects` and `data_connectors` carry a `path` column —
        # the others (`groups`, `users`) only have `slug` and (for users)
        # `path` too. Build the SQL accordingly.
        if entity_type in {"projects", "data_connectors"}:
            if "/" in identifier:
                namespace, _, slug = identifier.rpartition("/")
                rows = run_adhoc(
                    f"SELECT * FROM {table} "
                    "WHERE path = $id "
                    "OR (namespace = $ns AND slug = $slug) "
                    "OR slug = $id LIMIT 1",
                    {"id": identifier, "ns": namespace, "slug": slug},
                )
            else:
                rows = run_adhoc(
                    f"SELECT * FROM {table} "
                    "WHERE path = $id OR slug = $id LIMIT 1",
                    {"id": identifier},
                )
        elif entity_type == "users":
            rows = run_adhoc(
                f"SELECT * FROM {table} "
                "WHERE path = $id OR slug = $id LIMIT 1",
                {"id": identifier},
            )
        else:  # groups
            rows = run_adhoc(
                f"SELECT * FROM {table} WHERE slug = $id LIMIT 1",
                {"id": identifier},
            )
        if not rows:
            return []
        row = rows[0]
        pk_value = str(row.get(_ENTITY_PK[entity_type]) or "")
        if not pk_value:
            return []
        return [self._record_from_row(entity_type, pk_value, row)]

    def _lookup_by_slug_anywhere(self, slug: str) -> list[EntityRecord]:
        out: list[EntityRecord] = []
        for et in _ENTITY_NAMES:
            out.extend(self._lookup_by_path_or_id(et, slug))
        return out

    def _record_from_row(
        self,
        entity_type: str,
        entity_id: str,
        row: dict[str, Any],
    ) -> EntityRecord:
        return EntityRecord(
            index=self.name,
            entity_type=entity_type,
            id=entity_id,
            data=row,
            url=_entity_url(entity_type, row),
        )


_ENTITY_TABLE: dict[str, str] = {
    "projects": "projects",
    "groups": "groups",
    "users": "users",
    "data_connectors": "data_connectors",
}

_ENTITY_PK: dict[str, str] = {
    "projects": "project_id",
    "groups": "group_id",
    "users": "user_id",
    "data_connectors": "data_connector_id",
}


register(RenkulabAdapter())
