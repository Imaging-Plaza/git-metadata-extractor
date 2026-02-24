from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from datetime import datetime

AliasSource = Literal["ror", "agent", "manual", "derived"]


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    type: str
    data: dict[str, Any]
    identifiers: dict[str, Any]
    id_source: str
    provenance: list[dict[str, Any]]
    last_seen: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Edge:
    id: str
    source_id: str
    target_id: str
    relation_type: str
    data: dict[str, Any]
    provenance: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Alias:
    id: str
    alias_string: str
    canonical_entity_id: str
    confidence: float
    source: AliasSource
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AliasMatch:
    alias_id: str
    alias_string: str
    canonical_entity_id: str
    confidence: float
    source: AliasSource


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    source_url: str
    detected_type: str
    status: str
    stats: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None
    error_detail: str | None


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    entity_id: str
    field: str
    old_value: Any
    new_value: Any
    source: str
    run_id: str | None
    timestamp: datetime
