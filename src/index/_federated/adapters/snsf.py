"""Adapter wrapping `src.index.snsf` for federated search/lookup.

SNSF P3 indexes Swiss National Science Foundation grants, the people
involved, and the institutions receiving them.
"""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

# SNSF grant numbers are typically 6-7 digit integers, sometimes prefixed
# with a project type code (e.g. "10000-001234"). The public URL form is
# https://data.snf.ch/grants/grant/<id>.
_RE_SNSF_URL = re.compile(
    r"https?://(?:data|p3)\.sn[fs]\.ch/(?:grants/grant|grant)/(\S+?)(?:[/?#]|$)",
    re.I,
)
_RE_SNSF_ID = re.compile(r"\b(\d{6,7})\b")


class SnsfAdapter:
    name = "snsf"
    entity_types = ["grants"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002 — single-type for now
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        try:
            from src.index.snsf.config import load_config
            from src.index.snsf.query import query_rag_sync
        except Exception:  # noqa: BLE001
            return []
        cfg = load_config()
        kw: dict[str, Any] = {"top_k": top_k}
        if filters:
            for fk in ("institution", "discipline_l1", "state", "scope_mode"):
                if fk in filters and filters[fk] is not None:
                    kw[fk] = filters[fk]
        try:
            results = query_rag_sync(cfg, query, **kw)
        except Exception:  # noqa: BLE001
            return []
        out: list[Hit] = []
        for r in results:
            md = r.get("payload") or r.get("metadata") or r
            grant_id = md.get("grant_number") or md.get("grant_id") or md.get("id")
            if not grant_id:
                continue
            title = md.get("title") or md.get("project_title") or str(grant_id)
            score = float(r.get("rerank_score") or r.get("score") or 0.0)
            out.append(Hit(
                index=self.name, entity_type="grant",
                id=str(grant_id),
                title=str(title) if title else None,
                score=score,
                summary=_summary(md),
                url=f"https://data.snf.ch/grants/grant/{grant_id}",
                payload=dict(md),
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        s = identifier.strip()
        m = _RE_SNSF_URL.search(s)
        if m:
            grant_id_str = m.group(1)
        elif _RE_SNSF_ID.fullmatch(s):
            grant_id_str = s
        else:
            return []

        try:
            grant_id = int(grant_id_str)
        except ValueError:
            # URL-extracted id may include a non-numeric suffix; bail out
            # with the thin ack record so the caller still knows the URL parsed.
            return [EntityRecord(
                index=self.name, entity_type="grant", id=grant_id_str,
                data={"id": grant_id_str, "source": "data.snf.ch"},
                url=f"https://data.snf.ch/grants/grant/{grant_id_str}",
            )]

        try:
            from src.index.snsf.storage.duckdb_store import SnsfStore
        except Exception:  # noqa: BLE001
            return self._fallback_record(grant_id_str)

        try:
            store = SnsfStore.open()
        except Exception:  # noqa: BLE001
            return self._fallback_record(grant_id_str)
        try:
            row = store.fetch_grant(grant_id)
        except Exception:  # noqa: BLE001
            return self._fallback_record(grant_id_str)
        finally:
            store.close()

        if row is None:
            return []

        return [EntityRecord(
            index=self.name,
            entity_type="grant",
            id=str(row.get("grant_number") or grant_id),
            data=_compact_grant(row),
            url=f"https://data.snf.ch/grants/grant/{row.get('grant_number') or grant_id}",
        )]

    def _fallback_record(self, grant_id_str: str) -> list[EntityRecord]:
        """Thin ack when DuckDB is unreachable (e.g. concurrent writer lock)."""
        return [EntityRecord(
            index=self.name, entity_type="grant", id=grant_id_str,
            data={"id": grant_id_str, "source": "data.snf.ch"},
            url=f"https://data.snf.ch/grants/grant/{grant_id_str}",
        )]


def _summary(md: dict[str, Any]) -> str | None:
    parts = [
        str(md.get("title") or md.get("project_title") or ""),
        str(md.get("institution") or md.get("organization") or ""),
        str(md.get("discipline_l1") or ""),
        str(md.get("state") or ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


# Subset of `grants` columns kept in the federated EntityRecord.data payload —
# enough for an LLM caller to render a useful summary, without flooding
# downstream context with the full abstract or lay summaries.
_LOOKUP_COLS: tuple[str, ...] = (
    "grant_number",
    "title",
    "title_english",
    "responsible_applicant",
    "institute",
    "research_institution",
    "research_institution_type",
    "main_discipline",
    "main_discipline_l1",
    "main_discipline_l2",
    "main_field_of_research",
    "start_date",
    "end_date",
    "amount_granted",
    "keywords",
    "state",
    "funding_instrument",
    "call_full_title",
    "call_decision_year",
)


def _compact_grant(row: dict[str, Any]) -> dict[str, Any]:
    """Stringify timestamps / drop NULL columns so the result is JSON-clean."""
    out: dict[str, Any] = {}
    for col in _LOOKUP_COLS:
        v = row.get(col)
        if v is None:
            continue
        # DuckDB returns datetime/date objects for TIMESTAMP/DATE columns —
        # serialise them to ISO strings so the federated layer can json-dump.
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[col] = v
    return out


register(SnsfAdapter())
