"""Adapter wrapping `src.index.swissubase` for federated search/lookup.

The SWISSUbase index has four entity types — ``studies``, ``datasets``,
``persons``, ``institutions`` — all stored in a single Qdrant collection
``swissubase_entities`` with an ``entity_type`` payload field. The
adapter exposes them under the single index name ``swissubase`` and uses
the ``entity_type`` argument to scope the search; ``entity_type=None``
searches across all four (the default).

Lookup recognises three identifier shapes:

* ``https://www.swissubase.ch/{lang}/catalogue/studies/{N}`` URLs.
* Bare numeric IDs — interpreted as ``studyVersionId`` (the API's
  authoritative key).
* Internal person/institution composite keys
  (``swissubase:person:{N}`` / ``name:slugified``) when the upstream
  graph already contains them.
"""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

_RE_STUDY_URL = re.compile(
    r"https?://(?:www\.)?swissubase\.ch/[a-z]{2}/catalogue/studies/(\d+)",
    re.IGNORECASE,
)
_RE_NUMERIC_ID = re.compile(r"^\d+$")
_RE_PERSON_KEY = re.compile(r"^swissubase:person:\d+$")
_RE_NAME_SLUG = re.compile(r"^name:[a-z0-9-]+$")

_ENTITY_NAMES = ("studies", "datasets", "persons", "institutions")
_SWISSUBASE_LANG = "en"


def _study_url(study_id: str) -> str:
    return f"https://www.swissubase.ch/{_SWISSUBASE_LANG}/catalogue/studies/{study_id}"


def _hit_title(entity_type: str, payload: dict[str, Any]) -> str | None:
    if entity_type in {"studies", "datasets"}:
        return payload.get("title")
    if entity_type == "persons":
        return payload.get("display_name")
    if entity_type == "institutions":
        return payload.get("name")
    return None


def _hit_summary(entity_type: str, payload: dict[str, Any]) -> str | None:
    parts: list[str] = []
    if entity_type == "studies":
        ref = payload.get("ref")
        if ref:
            parts.append(f"ref:{ref}")
        disc = payload.get("main_discipline")
        if disc:
            parts.append(str(disc))
        progress = payload.get("progress")
        if progress:
            parts.append(str(progress))
        if payload.get("year_start") or payload.get("year_end"):
            parts.append(f"{payload.get('year_start') or '?'}-{payload.get('year_end') or '?'}")
    elif entity_type == "datasets":
        access = payload.get("access_right")
        if access:
            parts.append(f"access:{access}")
    elif entity_type == "persons":
        affil = payload.get("affiliation")
        if affil:
            parts.append(str(affil))
    elif entity_type == "institutions":
        ror = payload.get("ror_id")
        if ror:
            parts.append(f"ror:{ror}")
    return " — ".join(parts) if parts else None


def _hit_url(entity_type: str, payload: dict[str, Any]) -> str | None:
    """Prefer the embedded ``source_url`` (always populated for studies/datasets;
    empty for persons/institutions, which have no detail page on swissUbase)."""
    url = payload.get("source_url")
    if isinstance(url, str) and url:
        return url
    if entity_type == "studies" and payload.get("study_id"):
        return _study_url(str(payload["study_id"]))
    return None


class SwissubaseAdapter:
    name = "swissubase"
    entity_types: list[str] = list(_ENTITY_NAMES)  # noqa: RUF012

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        from src.index.swissubase.config import load_config
        from src.index.swissubase.retrieval.semantic import semantic_search

        if entity_type is not None and entity_type not in _ENTITY_NAMES:
            return []

        # The SWISSUbase Qdrant collection holds all four entity types,
        # disambiguated by the ``entity_type`` payload key. Push the
        # filter down so the ANN doesn't have to over-fetch.
        filter_payload: dict[str, Any] = dict(filters or {})
        if entity_type is not None:
            filter_payload["entity_type"] = entity_type

        try:
            results = semantic_search(
                config=load_config(),
                query=query,
                top_k=top_k,
                candidate_k=max(top_k * 5, 50),
                filter_payload=filter_payload or None,
            )
        except Exception:  # noqa: BLE001
            return []

        out: list[Hit] = []
        for r in results:
            payload = r.get("payload") or {}
            et = str(payload.get("entity_type") or "")
            entity_id = payload.get("entity_id") or r.get("id")
            if not entity_id or et not in _ENTITY_NAMES:
                continue
            out.append(Hit(
                index=self.name,
                entity_type=et,
                id=str(entity_id),
                title=_hit_title(et, payload),
                score=float(
                    r.get("rerank_score") or r.get("vector_score") or 0.0,
                ),
                summary=_hit_summary(et, payload),
                url=_hit_url(et, payload),
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        if not s:
            return []

        # SWISSUbase study URL → study_id (= studyVersionId).
        m = _RE_STUDY_URL.search(s)
        if m:
            return self._lookup_study(m.group(1))

        # Internal person key (swissubase:person:NNNN).
        if _RE_PERSON_KEY.match(s):
            return self._lookup_person(s)

        # Bare numeric → try studies first (most common downstream use).
        if _RE_NUMERIC_ID.match(s):
            records = self._lookup_study(s)
            if records:
                return records
            # Fall back to a person lookup using the personId composite.
            return self._lookup_person(f"swissubase:person:{s}")

        # `name:slugified` keys can be either a person or an institution.
        if _RE_NAME_SLUG.match(s):
            return self._lookup_person(s) + self._lookup_institution(s)

        return []

    def _lookup_study(self, study_id: str) -> list[EntityRecord]:
        try:
            from src.index.swissubase.storage.duckdb_store import SwissubaseStore
        except Exception:  # noqa: BLE001
            return []
        store = SwissubaseStore.open()
        try:
            row = store.fetch_study(study_id)
        except Exception:  # noqa: BLE001
            return []
        finally:
            store.close()
        if row is None:
            return []
        return [EntityRecord(
            index=self.name,
            entity_type="studies",
            id=str(study_id),
            data=row,
            url=str(row.get("source_url") or _study_url(study_id)),
        )]

    def _lookup_person(self, person_key: str) -> list[EntityRecord]:
        try:
            from src.index.swissubase.storage.duckdb_store import SwissubaseStore
        except Exception:  # noqa: BLE001
            return []
        store = SwissubaseStore.open()
        try:
            cur = store.connect().execute(
                "SELECT * FROM persons WHERE person_key = ?",
                [person_key],
            )
            row = cur.fetchone()
            if row is None:
                return []
            cols = [d[0] for d in cur.description]
            row_dict = dict(zip(cols, row, strict=False))
        except Exception:  # noqa: BLE001
            return []
        finally:
            store.close()
        return [EntityRecord(
            index=self.name,
            entity_type="persons",
            id=person_key,
            data=row_dict,
            url=row_dict.get("source_url"),
        )]

    def _lookup_institution(self, institution_key: str) -> list[EntityRecord]:
        try:
            from src.index.swissubase.storage.duckdb_store import SwissubaseStore
        except Exception:  # noqa: BLE001
            return []
        store = SwissubaseStore.open()
        try:
            cur = store.connect().execute(
                "SELECT * FROM institutions WHERE institution_key = ?",
                [institution_key],
            )
            row = cur.fetchone()
            if row is None:
                return []
            cols = [d[0] for d in cur.description]
            row_dict = dict(zip(cols, row, strict=False))
        except Exception:  # noqa: BLE001
            return []
        finally:
            store.close()
        return [EntityRecord(
            index=self.name,
            entity_type="institutions",
            id=institution_key,
            data=row_dict,
            url=row_dict.get("source_url"),
        )]


register(SwissubaseAdapter())
