from __future__ import annotations

from importlib import import_module
from typing import Any

from src.v2.observability.context import RunContext


def _get_logfire_module() -> Any | None:
    try:
        logfire_module = import_module("logfire")
    except ImportError:
        return None
    config = getattr(
        getattr(logfire_module, "DEFAULT_LOGFIRE_INSTANCE", None),
        "_config",
        None,
    )
    if getattr(config, "_initialized", False):
        return logfire_module
    return None


class V2Metrics:
    def __init__(self) -> None:
        self._logfire = _get_logfire_module()
        self._token_counter = (
            self._logfire.metric_counter(
                "v2.tokens.total",
                unit="tokens",
                description="Total token usage per agent and request",
            )
            if self._logfire is not None
            else None
        )
        self._stage_latency = (
            self._logfire.metric_histogram(
                "v2.stage.latency_ms",
                unit="ms",
                description="Stage execution latency in milliseconds",
            )
            if self._logfire is not None
            else None
        )
        self._validation_failures = (
            self._logfire.metric_counter(
                "v2.validation.failure_count",
                description="Strict validation failures by entity type",
            )
            if self._logfire is not None
            else None
        )
        self._alias_lookup_counter = (
            self._logfire.metric_counter(
                "v2.alias.lookup_count",
                description="Alias lookup hit/miss count",
            )
            if self._logfire is not None
            else None
        )
        self._graph_entity_upserts = (
            self._logfire.metric_counter(
                "v2.graph.upsert.entities",
                description="Graph entity upsert count",
            )
            if self._logfire is not None
            else None
        )
        self._graph_edge_upserts = (
            self._logfire.metric_counter(
                "v2.graph.upsert.edges",
                description="Graph edge upsert count",
            )
            if self._logfire is not None
            else None
        )

    def _run_id_attributes(self) -> dict[str, Any]:
        run_id = RunContext.get_run_id()
        if not run_id:
            return {}
        return {"run_id": run_id}

    def record_tokens(
        self,
        agent_name: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        if self._token_counter is None:
            return
        prompt = prompt_tokens or 0
        completion = completion_tokens or 0
        total_tokens = prompt + completion
        self._token_counter.add(
            total_tokens,
            {
                **self._run_id_attributes(),
                "agent_name": agent_name,
                "tokens_prompt": prompt,
                "tokens_completion": completion,
            },
        )

    def record_stage_latency(self, stage_name: str, duration_ms: int) -> None:
        if self._stage_latency is None:
            return
        self._stage_latency.record(
            duration_ms,
            {
                **self._run_id_attributes(),
                "stage_name": stage_name,
            },
        )

    def record_validation_failure(self, entity_type: str) -> None:
        if self._validation_failures is None:
            return
        self._validation_failures.add(
            1,
            {
                **self._run_id_attributes(),
                "entity_type": entity_type,
            },
        )

    def record_alias_lookup(self, hit: bool) -> None:  # noqa: FBT001
        if self._alias_lookup_counter is None:
            return
        self._alias_lookup_counter.add(
            1,
            {
                **self._run_id_attributes(),
                "result": "hit" if hit else "miss",
            },
        )

    def record_graph_upsert(self, entity_count: int, edge_count: int) -> None:
        if self._graph_entity_upserts is not None:
            self._graph_entity_upserts.add(
                entity_count,
                self._run_id_attributes(),
            )
        if self._graph_edge_upserts is not None:
            self._graph_edge_upserts.add(
                edge_count,
                self._run_id_attributes(),
            )
