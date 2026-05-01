"""Pydantic models for cross-stage transfer.

The on-disk JSONL is the canonical record format; these models cover
in-memory data passed between stages and the public query result shape.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RorRecord(BaseModel):
    """A ROR v2 record kept verbatim. We expose a few accessors for convenience."""

    id: str
    raw: Dict[str, Any]

    @property
    def names(self) -> List[Dict[str, Any]]:
        names = self.raw.get("names")
        return names if isinstance(names, list) else []

    @property
    def display_name(self) -> Optional[str]:
        for entry in self.names:
            if "ror_display" in (entry.get("types") or []):
                value = entry.get("value")
                if isinstance(value, str) and value:
                    return value
        for entry in self.names:
            value = entry.get("value")
            if isinstance(value, str) and value:
                return value
        return None

    @property
    def country_code(self) -> Optional[str]:
        for loc in self.raw.get("locations") or []:
            details = loc.get("geonames_details") if isinstance(loc, dict) else None
            if isinstance(details, dict):
                cc = details.get("country_code")
                if isinstance(cc, str) and cc:
                    return cc
        return None


class IndexedRecord(BaseModel):
    """One row of records.jsonl. Row index = FAISS row index."""

    row: int
    ror_id: str
    name: Optional[str]
    text: str
    record: Dict[str, Any]


class IndexManifest(BaseModel):
    """manifest.json — describes the build that produced an index."""

    scope_mode: str
    record_count: int
    embedding_model: str
    embedding_dim: int
    reranker_model: str
    ror_release_version: Optional[str] = None
    ror_release_doi: Optional[str] = None
    built_at_iso: str


class ScoredRecord(BaseModel):
    """Public query result shape."""

    ror_id: str
    name: Optional[str]
    score: float
    record: Dict[str, Any]


class DumpMatch(BaseModel):
    """Public lookup result shape (no score from semantic model)."""

    ror_id: str
    name: Optional[str]
    record: Dict[str, Any]
    matched_tokens: List[str] = Field(default_factory=list)
