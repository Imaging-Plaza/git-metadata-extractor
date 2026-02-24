from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.v2.observability import error_events
from src.v2.observability.context import RunContext
from src.v2.providers.base import ProviderNotFoundError


@dataclass
class _CapturedEvent:
    message: str
    attributes: dict[str, Any]


@dataclass
class _FakeLogfire:
    events: list[_CapturedEvent] = field(default_factory=list)

    def error(self, message: str, **attributes: Any) -> None:
        self.events.append(_CapturedEvent(message=message, attributes=attributes))


def test_record_error_emits_provider_error_event_with_context(monkeypatch, caplog) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(error_events, "_get_logfire_module", lambda: fake_logfire)

    caplog.set_level("ERROR", logger=error_events.logger.name)
    error_events.record_error(
        stage="context_gather",
        error=ProviderNotFoundError("provider unavailable"),
        run_id="run-ctx-1",
        source_url="https://github.com/owner/repo",
        detected_type="repository",
    )

    assert len(fake_logfire.events) == 1
    event = fake_logfire.events[0]
    assert event.message == "v2.error"
    assert event.attributes["stage"] == "context_gather"
    assert event.attributes["error_class"] == "ProviderNotFoundError"
    assert event.attributes["run_id"] == "run-ctx-1"
    assert event.attributes["source_url"] == "https://github.com/owner/repo"

    assert caplog.records
    assert caplog.records[-1].run_id == "run-ctx-1"


def test_record_error_emits_validation_details(monkeypatch) -> None:
    fake_logfire = _FakeLogfire()
    monkeypatch.setattr(error_events, "_get_logfire_module", lambda: fake_logfire)

    error_events.record_error(
        stage="strict_validation",
        error=ValueError("missing required field"),
        run_id="run-ctx-2",
        source_url="https://github.com/owner/repo",
        detected_type="repository",
        details={"field": "schema:name", "constraint": "required"},
    )

    event = fake_logfire.events[0]
    assert event.attributes["stage"] == "strict_validation"
    assert event.attributes["field"] == "schema:name"
    assert event.attributes["constraint"] == "required"


def test_record_error_uses_context_run_id_and_logs_without_logfire(
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setattr(error_events, "_get_logfire_module", lambda: None)
    context_token = RunContext.set_run_id("run-ctx-3")
    caplog.set_level("ERROR", logger=error_events.logger.name)

    error_events.record_error(
        stage="pipeline_execute",
        error=RuntimeError("pipeline crashed"),
        source_url="https://github.com/owner/repo",
        detected_type="repository",
    )

    assert caplog.records
    assert caplog.records[-1].run_id == "run-ctx-3"
    RunContext.reset(context_token)
