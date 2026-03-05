from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ContextBundle:
    detected_type: str
    context: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected_type": self.detected_type,
            "context": dict(self.context),
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class ReconciledEntities:
    entities: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    memberships: list[dict[str, Any]] = field(default_factory=list)
    contributions: list[dict[str, Any]] = field(default_factory=list)
    link_warnings: list[str] = field(default_factory=list)
    synthesis_warnings: list[str] = field(default_factory=list)
    reconciliation_debug: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AssembledOutput:
    root_entity: dict[str, Any] | None
    related_entities: list[dict[str, Any]] = field(default_factory=list)
    excluded_entities: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LLMDedupStageResult:
    typed_entity_buckets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    candidate_clusters: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    resolution: dict[str, Any] = field(default_factory=dict)
    accepted_cluster_count: int = 0
    rejected_cluster_count: int = 0
    remap_count: int = 0


@dataclass(slots=True)
class LLMCriticStageResult:
    reconciled: ReconciledEntities = field(default_factory=ReconciledEntities)
    warnings: list[str] = field(default_factory=list)
    decisions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    applied: dict[str, Any] = field(default_factory=dict)
    pruned_excluded_entities: list[dict[str, Any]] = field(default_factory=list)
