from __future__ import annotations

from typing import Any

from pydantic import Field

from git_metadata_extractor.models import InternalModel


class ContextBundle(InternalModel):
    detected_type: str
    context: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ReconciledEntities(InternalModel):
    entities: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    memberships: list[dict[str, Any]] = Field(default_factory=list)
    contributions: list[dict[str, Any]] = Field(default_factory=list)
    link_warnings: list[str] = Field(default_factory=list)
    reconciliation_debug: dict[str, Any] = Field(default_factory=dict)


class AssembledOutput(InternalModel):
    root_entity: dict[str, Any] | None
    related_entities: list[dict[str, Any]] = Field(default_factory=list)
    excluded_entities: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LLMDedupStageResult(InternalModel):
    typed_entity_buckets: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    candidate_clusters: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    resolution: dict[str, Any] = Field(default_factory=dict)
    accepted_cluster_count: int = 0
    rejected_cluster_count: int = 0
    remap_count: int = 0


class LLMCriticStageResult(InternalModel):
    reconciled: ReconciledEntities = Field(default_factory=ReconciledEntities)
    warnings: list[str] = Field(default_factory=list)
    decisions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    applied: dict[str, Any] = Field(default_factory=dict)
    pruned_excluded_entities: list[dict[str, Any]] = Field(default_factory=list)
