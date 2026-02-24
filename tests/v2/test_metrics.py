from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.v2.observability import metrics as metrics_module
from src.v2.observability.context import RunContext
from src.v2.observability.metrics import V2Metrics

TOKEN_TOTAL = 15
STAGE_LATENCY = 42
ENTITY_UPSERT_COUNT = 3
EDGE_UPSERT_COUNT = 9


@dataclass
class _MetricCall:
    amount: float
    attributes: dict[str, Any] | None


@dataclass
class _FakeCounter:
    calls: list[_MetricCall] = field(default_factory=list)

    def add(
        self,
        amount: float,
        attributes: dict[str, Any] | None = None,
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        self.calls.append(_MetricCall(amount=amount, attributes=attributes))


@dataclass
class _FakeHistogram:
    calls: list[_MetricCall] = field(default_factory=list)

    def record(
        self,
        amount: float,
        attributes: dict[str, Any] | None = None,
        *_args: Any,
        **_kwargs: Any,
    ) -> None:
        self.calls.append(_MetricCall(amount=amount, attributes=attributes))


class _FakeLogfire:
    def __init__(self) -> None:
        self.counters: dict[str, _FakeCounter] = {}
        self.histograms: dict[str, _FakeHistogram] = {}

    def metric_counter(self, name: str, **_kwargs: Any) -> _FakeCounter:
        return self.counters.setdefault(name, _FakeCounter())

    def metric_histogram(self, name: str, **_kwargs: Any) -> _FakeHistogram:
        return self.histograms.setdefault(name, _FakeHistogram())


def test_metrics_emit_tokens_stage_latency_and_counters(monkeypatch) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(metrics_module, "_get_logfire_module", lambda: fake_logfire)
    context_token = RunContext.set_run_id("run-1")
    metrics = V2Metrics()

    metrics.record_tokens("person_agent", 10, 5)
    metrics.record_stage_latency("context_gather", 42)
    metrics.record_validation_failure("person")
    metrics.record_alias_lookup(hit=True)
    metrics.record_alias_lookup(hit=False)
    metrics.record_graph_upsert(entity_count=3, edge_count=9)

    token_counter = fake_logfire.counters["v2.tokens.total"]
    assert token_counter.calls[0].amount == TOKEN_TOTAL
    assert token_counter.calls[0].attributes == {
        "run_id": "run-1",
        "agent_name": "person_agent",
        "tokens_prompt": 10,
        "tokens_completion": 5,
    }

    latency_histogram = fake_logfire.histograms["v2.stage.latency_ms"]
    assert latency_histogram.calls[0].amount == STAGE_LATENCY
    assert latency_histogram.calls[0].attributes == {
        "run_id": "run-1",
        "stage_name": "context_gather",
    }

    validation_counter = fake_logfire.counters["v2.validation.failure_count"]
    assert validation_counter.calls[0].amount == 1
    assert validation_counter.calls[0].attributes == {
        "run_id": "run-1",
        "entity_type": "person",
    }

    alias_counter = fake_logfire.counters["v2.alias.lookup_count"]
    assert [call.attributes for call in alias_counter.calls] == [
        {"run_id": "run-1", "result": "hit"},
        {"run_id": "run-1", "result": "miss"},
    ]

    entity_counter = fake_logfire.counters["v2.graph.upsert.entities"]
    edge_counter = fake_logfire.counters["v2.graph.upsert.edges"]
    assert entity_counter.calls[0].amount == ENTITY_UPSERT_COUNT
    assert edge_counter.calls[0].amount == EDGE_UPSERT_COUNT
    assert entity_counter.calls[0].attributes == {"run_id": "run-1"}
    assert edge_counter.calls[0].attributes == {"run_id": "run-1"}

    RunContext.reset(context_token)


def test_metrics_noop_when_logfire_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(metrics_module, "_get_logfire_module", lambda: None)
    metrics = V2Metrics()

    metrics.record_tokens("agent", 1, 2)
    metrics.record_stage_latency("stage", 1)
    metrics.record_validation_failure("entity")
    metrics.record_alias_lookup(hit=True)
    metrics.record_graph_upsert(1, 1)
