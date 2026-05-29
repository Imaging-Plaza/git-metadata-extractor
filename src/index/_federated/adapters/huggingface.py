"""Adapter wrapping `src.index.huggingface` for federated search/lookup."""

from __future__ import annotations

import re
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

# HF identifiers we know how to recognise. The lookup() method tries each in
# order against the input string.
_RE_HF_URL_REPO = re.compile(
    r"https?://(?:huggingface\.co|hf\.co)/(?:datasets|spaces)/([^/\s?#]+)/([^/\s?#]+)",
    re.IGNORECASE,
)
_RE_HF_URL_MODEL = re.compile(
    r"https?://(?:huggingface\.co|hf\.co)/([^/\s?#]+)/([^/\s?#]+)",
    re.IGNORECASE,
)
_RE_HF_URL_NS = re.compile(
    r"https?://(?:huggingface\.co|hf\.co)/([^/\s?#]+)/?$",
    re.IGNORECASE,
)


class HuggingFaceAdapter:
    name = "huggingface"
    entity_types = ["models", "datasets", "spaces", "orgs"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        # Lazy import — keeps `gme indices` cheap when HF deps aren't installed.
        from src.index.huggingface.config import load_config
        from src.index.huggingface.retrieval.semantic import semantic_search

        config = load_config()
        config.require_rcp()
        types_to_query = [entity_type] if entity_type else ["models"]
        out: list[Hit] = []
        for et in types_to_query:
            try:
                results = semantic_search(
                    config=config, query=query, entity_type=et,
                    top_k=top_k, candidate_k=max(top_k * 5, 50),
                    filter_payload=filters,
                )
            except Exception:  # noqa: BLE001 — federation should be fault-tolerant
                continue
            for r in results:
                payload = r.get("payload") or {}
                rid = payload.get("repo_id")
                if not rid:
                    continue
                singular = (payload.get("entity_type") or et.rstrip("s") or et)
                out.append(Hit(
                    index=self.name,
                    entity_type=singular,
                    id=rid,
                    title=payload.get("fullname") or rid,
                    score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                    summary=_summary_from_payload(payload),
                    url=_canonical_url(singular, rid),
                    payload=payload,
                ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        # Strip surrounding whitespace and trailing slashes.
        s = identifier.strip().rstrip("/")
        repo_id = None
        slug = None
        # Patterns: full URLs first, then bare slugs.
        m = _RE_HF_URL_REPO.search(s) or _RE_HF_URL_MODEL.search(s)
        if m:
            repo_id = f"{m.group(1)}/{m.group(2)}"
            slug = m.group(1)
        else:
            m_ns = _RE_HF_URL_NS.search(s)
            if m_ns:
                slug = m_ns.group(1)
            elif "/" in s:
                # Plain "<author>/<repo>" form.
                author, _, repo = s.partition("/")
                if author and repo:
                    repo_id = s
                    slug = author
            else:
                # Plain namespace.
                slug = s

        records: list[EntityRecord] = []
        try:
            from src.index.huggingface.storage.duckdb_store import HuggingFaceStore
        except Exception:  # noqa: BLE001
            return records
        store = HuggingFaceStore.open()

        if repo_id:
            for table in ("models", "datasets", "spaces"):
                row = store.fetch_repo(table, repo_id)
                if row is not None:
                    records.append(EntityRecord(
                        index=self.name,
                        entity_type=table.rstrip("s") or table,
                        id=repo_id,
                        data=row,
                        url=_canonical_url(table.rstrip("s"), repo_id),
                    ))

        if slug:
            org_row = store.fetch_org(slug)
            if org_row is not None:
                records.append(EntityRecord(
                    index=self.name,
                    entity_type="org",
                    id=slug,
                    data=org_row,
                    url=f"https://huggingface.co/{slug}",
                ))
        return records


def _summary_from_payload(payload: dict[str, Any]) -> str | None:
    parts = [
        str(payload.get("repo_id") or ""),
        str(payload.get("fullname") or ""),
        str(payload.get("pipeline_tag") or ""),
        str(payload.get("license") or ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


def _canonical_url(singular: str, repo_id: str) -> str | None:
    if not repo_id:
        return None
    if singular == "model":
        return f"https://huggingface.co/{repo_id}"
    if singular == "dataset":
        return f"https://huggingface.co/datasets/{repo_id}"
    if singular == "space":
        return f"https://huggingface.co/spaces/{repo_id}"
    if singular == "org":
        return f"https://huggingface.co/{repo_id}"
    return f"https://huggingface.co/{repo_id}"


register(HuggingFaceAdapter())
