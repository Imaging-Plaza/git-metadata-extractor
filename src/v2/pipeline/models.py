from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.v2.agents.models import AgentResult, TypedEntityBuckets, infer_entity_bucket


@dataclass(slots=True)
class AgentGroup:
    name: str
    agent_keys: list[str] = field(default_factory=list)
    parallelizable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent_keys": list(self.agent_keys),
            "parallelizable": self.parallelizable,
        }


@dataclass(slots=True)
class Stage:
    name: str
    groups: list[AgentGroup] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "groups": [group.to_dict() for group in self.groups],
            "depends_on": list(self.depends_on),
        }


@dataclass(slots=True)
class ExecutionPlan:
    detected_type: str
    stages: list[Stage] = field(default_factory=list)

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected_type": self.detected_type,
            "stages": [stage.to_dict() for stage in self.stages],
            "has_cycle": self.has_circular_dependencies(),
        }


@dataclass(slots=True)
class PipelineResult:
    stages_completed: list[str] = field(default_factory=list)
    agent_results: dict[str, AgentResult] = field(default_factory=dict)
    typed_entity_buckets: TypedEntityBuckets = field(default_factory=TypedEntityBuckets)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0

    def resolved_typed_entity_buckets(self) -> TypedEntityBuckets:
        resolved = TypedEntityBuckets()
        resolved.merge(self.typed_entity_buckets)

        for result_key, result in self.agent_results.items():
            if not isinstance(result.data, dict) or not result.data:
                continue
            bucket_name = infer_entity_bucket(agent_key=result_key, data=result.data)
            if bucket_name is None:
                continue
            resolved.add(bucket_name, result.data)

        return resolved

    def to_dict(self) -> dict[str, Any]:
        serialized_results: dict[str, dict[str, Any]] = {}
        for key, result in self.agent_results.items():
            serialized_results[key] = {
                "data": result.data,
                "warnings": list(result.warnings),
                "raw_output": result.raw_output,
                "is_partial": result.is_partial,
                "failure_reason": result.failure_reason,
                "model": result.model,
                "provider": result.provider,
                "tokens_prompt": result.tokens_prompt,
                "tokens_completion": result.tokens_completion,
                "stats": dict(result.stats),
            }

        typed_entity_buckets = self.resolved_typed_entity_buckets().to_dict()
        return {
            "stages_completed": list(self.stages_completed),
            "agent_results": serialized_results,
            "typed_entity_buckets": typed_entity_buckets,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "duration_ms": self.duration_ms,
        }
