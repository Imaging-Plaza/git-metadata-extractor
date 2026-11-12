from __future__ import annotations

from typing import Any

from pydantic import Field

from git_metadata_extractor.agents.models import (
    AgentResult,
    TypedEntityBuckets,
    infer_entity_bucket,
    normalize_entity_bucket_key,
)
from git_metadata_extractor.models import InternalModel


class AgentGroup(InternalModel):
    name: str
    agent_keys: list[str] = Field(default_factory=list)


class Stage(InternalModel):
    name: str
    groups: list[AgentGroup] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ExecutionPlan(InternalModel):
    detected_type: str
    stages: list[Stage] = Field(default_factory=list)

    def has_circular_dependencies(self) -> bool:
        stage_map = {stage.name: stage for stage in self.stages}
        visiting: set[str] = set()
        visited: set[str] = set()

        def _visit(stage_name: str) -> bool:
            if stage_name in visited:
                return False
            if stage_name in visiting:
                return True

            visiting.add(stage_name)
            stage = stage_map.get(stage_name)
            if stage is None:
                visiting.remove(stage_name)
                visited.add(stage_name)
                return False

            has_cycle = any(_visit(dep) for dep in stage.depends_on)
            visiting.remove(stage_name)
            visited.add(stage_name)
            return has_cycle

        return any(_visit(stage.name) for stage in self.stages)


class PipelineResult(InternalModel):
    stages_completed: list[str] = Field(default_factory=list)
    agent_results: dict[str, AgentResult] = Field(default_factory=dict)
    typed_entity_buckets: TypedEntityBuckets = Field(default_factory=TypedEntityBuckets)
    gathered_context: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    duration_ms: int = 0

    def resolved_typed_entity_buckets(self) -> TypedEntityBuckets:
        resolved = TypedEntityBuckets()
        resolved.merge(self.typed_entity_buckets)

        for result_key, result in self.agent_results.items():
            if not isinstance(result.data, dict) or not result.data:
                pass
            else:
                bucket_name = infer_entity_bucket(agent_key=result_key, data=result.data)
                if bucket_name is not None:
                    resolved.add(bucket_name, result.data)

            if not isinstance(result.stats, dict):
                continue
            for stats_key, stats_value in result.stats.items():
                bucket_name = normalize_entity_bucket_key(stats_key)
                if bucket_name is None or not isinstance(stats_value, list):
                    continue
                for entity in stats_value:
                    if isinstance(entity, dict) and entity:
                        resolved.add(bucket_name, entity)

        return resolved

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump() | {
            "typed_entity_buckets": self.resolved_typed_entity_buckets().to_dict(),
        }
