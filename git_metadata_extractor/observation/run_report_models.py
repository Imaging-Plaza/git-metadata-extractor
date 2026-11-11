"""Versioned, payload-free extraction observation contracts."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, NonNegativeFloat, NonNegativeInt

from git_metadata_extractor.models import DataModel

StagePhase = Literal["generation", "post_processing"]
StageStatus = Literal["completed", "partial", "failed", "skipped", "cancelled"]
ValidationStatus = Literal["passed", "violations", "unavailable", "error"]
ReportStatus = Literal["completed", "failed", "cache_hit"]
Runtime = Literal["llm", "hybrid", "rule_based", "unknown"]


class ChangeSummary(DataModel):
    """Entity counts and distinct property names; never values or identifiers."""

    entities_added: NonNegativeInt = 0
    entities_removed: NonNegativeInt = 0
    entities_changed: NonNegativeInt = 0
    properties_added: list[str] = Field(default_factory=list)
    properties_removed: list[str] = Field(default_factory=list)
    properties_changed: list[str] = Field(default_factory=list)


class StageReport(DataModel):
    """One invocation in observed order, including repeated stage names."""

    name: str
    phase: StagePhase
    status: StageStatus = "completed"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed_ms: NonNegativeFloat = 0
    reason: str | None = None
    error_type: str | None = None
    activity_ids: list[str] = Field(default_factory=list)
    changes: ChangeSummary | None = None


class ActivityReport(DataModel):
    activity_id: str
    name: str
    kind: str
    status: str
    run_id: str | None = None
    parent_activity_id: str | None = None
    model: str | None = None
    provider: str | None = None
    historical: bool = False
    cache_hit: bool = False
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed_ms: NonNegativeFloat | None = None
    mapping_version: str | None = None
    mapping_fingerprint: str | None = None
    source_contract: str | None = None
    target_contract: str | None = None
    tokens_prompt: NonNegativeInt | None = None
    tokens_completion: NonNegativeInt | None = None
    requests: NonNegativeInt | None = None
    tool_calls: NonNegativeInt | None = None


class RetrievalReport(DataModel):
    query_id: str
    activity_id: str | None = None
    service: str
    status: str
    cache_hit: bool = False
    historical: bool = False
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed_ms: NonNegativeFloat | None = None
    sources: list[str] = Field(default_factory=list)


class ValidationReport(DataModel):
    name: str
    status: ValidationStatus
    checked: NonNegativeInt | None = None
    violations: NonNegativeInt | None = None
    warnings: NonNegativeInt = 0
    reason: str | None = None


class UsageSummary(DataModel):
    """Known current-model subtotals; complete=false means unknown usage remains."""

    model_calls: NonNegativeInt = 0
    tokens_prompt: NonNegativeInt = 0
    tokens_completion: NonNegativeInt = 0
    requests: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    complete: bool = True
    missing_usage_activities: NonNegativeInt = 0


class AttributionSummary(DataModel):
    """Trace claim counts, not proof of final-graph coverage or correctness."""

    recorded_claims: NonNegativeInt = 0
    cited_claims: NonNegativeInt = 0
    matching_only_claims: NonNegativeInt = 0
    uncited_claims: NonNegativeInt = 0
    historical_claims: NonNegativeInt = 0


class RunReport(DataModel):
    schema_version: Literal[1] = 1
    request_run_id: str
    output_run_id: str | None
    runtime: Runtime
    status: ReportStatus
    elapsed_ms: NonNegativeFloat
    warning_count: NonNegativeInt = 0
    stages: list[StageReport] = Field(default_factory=list)
    activities: list[ActivityReport] = Field(default_factory=list)
    retrievals: list[RetrievalReport] = Field(default_factory=list)
    validations: list[ValidationReport] = Field(default_factory=list)
    usage: UsageSummary = Field(default_factory=UsageSummary)
    attribution: AttributionSummary = Field(default_factory=AttributionSummary)
    limitations: list[str] = Field(default_factory=list)
