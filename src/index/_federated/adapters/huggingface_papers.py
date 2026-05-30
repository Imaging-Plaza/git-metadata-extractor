"""Adapter wrapping `src.index.huggingface_papers` for federated search/lookup."""

from __future__ import annotations

from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register


class HuggingFacePapersAdapter:
    name = "huggingface_papers"
    entity_types = ["paper"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002 — single-type index
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        try:
            from src.index.huggingface_papers.config import load_config
            from src.index.huggingface_papers.retrieval.semantic import semantic_search
        except Exception:  # noqa: BLE001
            return []
        try:
            cfg = load_config()
            results = semantic_search(
                config=cfg, query=query,
                top_k=top_k, candidate_k=max(top_k * 5, 50),
                filter_payload=filters,
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[Hit] = []
        for r in results:
            payload = r.get("payload") or {}
            arxiv_id = payload.get("arxiv_id")
            if not arxiv_id:
                continue
            if r.get("entity") is None:
                continue
            out.append(Hit(
                index=self.name, entity_type="paper",
                id=str(arxiv_id),
                title=payload.get("title") or str(arxiv_id),
                score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                summary=_paper_summary(payload),
                url=f"https://huggingface.co/papers/{arxiv_id}",
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:
        """Lookup by any arXiv id shape — bare, versioned, arXiv URL,
        HF Papers URL, `arxiv:` tag, or arXiv DOI. The canonical
        normaliser handles every wire form."""
        try:
            from src.index.huggingface_papers.ingest.hf_papers_client import (
                normalize_arxiv_id,
            )
            from src.index.huggingface_papers.storage.duckdb_store import (
                HuggingFacePapersStore,
            )
        except Exception:  # noqa: BLE001
            return []
        arxiv_id = normalize_arxiv_id(identifier)
        if arxiv_id is None:
            return []
        store = HuggingFacePapersStore.open()
        if hasattr(store, "fetch_paper"):
            row = store.fetch_paper(arxiv_id)
            if row is not None:
                return [EntityRecord(
                    index=self.name, entity_type="paper", id=arxiv_id,
                    data=row,
                    url=f"https://huggingface.co/papers/{arxiv_id}",
                )]
        return []


def _paper_summary(payload: dict[str, Any]) -> str | None:
    parts = []
    upvotes = payload.get("upvotes")
    if isinstance(upvotes, int) and upvotes > 0:
        parts.append(f"{upvotes} upvotes")
    doi = payload.get("doi")
    if isinstance(doi, str) and doi:
        parts.append(doi)
    return " — ".join(parts) if parts else None


register(HuggingFacePapersAdapter())
