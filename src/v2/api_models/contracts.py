from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.v2.api_models.errors import V2ErrorResponse
from src.v2.observation.github_rate_limit import GitHubRateLimitSummary


class V2Stats(BaseModel):
    entities_count: int
    triples_count: int
    run_id: str
    duration_ms: int
    stages_completed: list[str] = Field(default_factory=list)


class V2JSONLDOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    context: dict[str, Any] = Field(alias="@context")
    graph: list[dict[str, Any]] = Field(alias="@graph")
    excluded_entities: list[dict[str, Any]] | None = None


class V2JSONOutputEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_entity: dict[str, Any] | None
    related_entities: list[dict[str, Any]]
    excluded_entities: list[dict[str, Any]]
    entities_by_type: dict[str, list[dict[str, Any]]]


class V2ExtractRequest(BaseModel):
    source_url: str
    output_format: Literal["jsonld", "json"] = "jsonld"
    agent_runtime: Literal["rule_based", "llm", "hybrid"] | None = None
    include_context_summary: bool = False


class V2ExtractResponse(BaseModel):
    source_url: str
    detected_type: Literal["repository", "user", "organization"]
    output_format: Literal["jsonld", "json"]
    output: V2JSONLDOutput | V2JSONOutputEnvelope
    context_summary_markdown: str | None = None
    warnings: list[str] = Field(default_factory=list)
    stats: V2Stats

    @model_validator(mode="after")
    def output_matches_format(self) -> V2ExtractResponse:
        if self.output_format == "jsonld" and not isinstance(self.output, V2JSONLDOutput):
            message = "output must match jsonld contract when output_format=jsonld"
            raise ValueError(message)
        if self.output_format == "json" and not isinstance(self.output, V2JSONOutputEnvelope):
            message = "output must match json envelope contract when output_format=json"
            raise ValueError(message)
        return self


class V2HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    components: dict[str, Literal["healthy", "degraded", "unhealthy"]]
    version: str
    github_rate_limit: GitHubRateLimitSummary | None = None


class V2ExtractJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class V2ExtractJob(BaseModel):
    job_id: str
    status: V2ExtractJobStatus
    request: V2ExtractRequest
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: V2ExtractResponse | None = None
    error: V2ErrorResponse | None = None


class V2ExtractJobAccepted(BaseModel):
    job_id: str
    status: V2ExtractJobStatus
    status_url: str
    submitted_at: datetime
