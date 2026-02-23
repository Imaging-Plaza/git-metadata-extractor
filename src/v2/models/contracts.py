from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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


class V2ExtractResponse(BaseModel):
    source_url: str
    detected_type: Literal["repository", "user", "organization"]
    output_format: Literal["jsonld", "json"]
    output: dict[str, Any] | list[Any]
    graph_update: V2GraphUpdate | None = None
    warnings: list[str] = Field(default_factory=list)
    stats: V2Stats
    intermediates: list[dict[str, Any]] | None = None


class V2GraphResponse(BaseModel):
    graph_jsonld: dict[str, Any]
    intermediates: list[dict[str, Any]] | None = None
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
