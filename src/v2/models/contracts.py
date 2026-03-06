from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IntermediateEnvelope(BaseModel):
    agent_name: str
    run_id: str | None = None
    timestamp: str
    data: dict[str, Any]


class V2Stats(BaseModel):
    entities_count: int
    triples_count: int
    run_id: str
    duration_ms: int
    stages_completed: list[str] = Field(default_factory=list)


class V2GraphUpdate(BaseModel):
    entities_upserted: int
    edges_upserted: int
    aliases_added: int


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
    agent_runtime: Literal["rule_based", "llm"] | None = None
    include_intermediates: bool = False
    include_context_summary: bool = False


class V2ExtractResponse(BaseModel):
    source_url: str
    detected_type: Literal["repository", "user", "organization"]
    output_format: Literal["jsonld", "json"]
    output: V2JSONLDOutput | V2JSONOutputEnvelope
    context_summary_markdown: str | None = None
    graph_update: V2GraphUpdate | None = None
    warnings: list[str] = Field(default_factory=list)
    stats: V2Stats
    intermediates: list[IntermediateEnvelope] | None = None

    @model_validator(mode="after")
    def output_matches_format(self) -> V2ExtractResponse:
        if self.output_format == "jsonld" and not isinstance(self.output, V2JSONLDOutput):
            message = "output must match jsonld contract when output_format=jsonld"
            raise ValueError(message)
        if self.output_format == "json" and not isinstance(self.output, V2JSONOutputEnvelope):
            message = "output must match json envelope contract when output_format=json"
            raise ValueError(message)
        return self


class V2GraphResponse(BaseModel):
    graph_jsonld: dict[str, Any]
    intermediates: list[IntermediateEnvelope] | None = None
    stats: V2Stats

    @field_validator("graph_jsonld")
    @classmethod
    def graph_jsonld_requires_context_and_graph(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        required_keys = {"@context", "@graph"}
        if not required_keys.issubset(value):
            message = "graph_jsonld must contain '@context' and '@graph' keys"
            raise ValueError(message)
        return value


class V2HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    components: dict[str, Literal["healthy", "degraded", "unhealthy"]]
    version: str
