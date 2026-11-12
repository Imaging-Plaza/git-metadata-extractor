"""Opt-in request-local observation, independent of semantic evidence scopes.

Only enabled reports inspect entity snapshots. Evidence supplies activity and
retrieval metadata, while this recorder supplies actual stage and validation
outcomes. Neither report construction nor observation starts provider work.
"""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
from time import perf_counter
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from git_metadata_extractor.models import DataModel
from git_metadata_extractor.observation.evidence import EvidenceTrace, get_trace
from git_metadata_extractor.observation.run_report_models import (
    ActivityReport,
    AttributionSummary,
    ChangeSummary,
    ReportStatus,
    RetrievalReport,
    RunReport,
    Runtime,
    StagePhase,
    StageReport,
    StageStatus,
    UsageSummary,
    ValidationReport,
    ValidationStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

__all__ = [
    "RunReport", "StageReport", "build_run_report", "observe_stage",
    "record_skipped", "record_stage_outcome", "record_validation", "report_scope", "start_report",
]

_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+\-]{0,255}\Z")
_PROPERTY = re.compile(r"(?:schema|pulse|org|time|rdf|rdfs|owl|skos|dcterms|foaf):[A-Za-z][A-Za-z0-9_\-]*\Z")
_USAGE_FIELDS = ("tokens_prompt", "tokens_completion", "requests", "tool_calls")
_LIMITATIONS = [
    "Retrieval proves access, not derivation; only checked citations establish copied values.",
    "Attribution counts recorded trace claims, not final-graph coverage or factual correctness.",
    "Change summaries compare stage boundaries and omit intermediate mutations and raw values.",
    "Usage includes only observed current-run model calls; incomplete totals are known subtotals.",
]


class ReportRecorder(DataModel):
    run_id: str | None = None
    runtime: Runtime = "unknown"
    started: float = Field(default_factory=perf_counter)
    stages: list[StageReport] = Field(default_factory=list)
    validations: list[ValidationReport] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


_recorder: ContextVar[ReportRecorder | None] = ContextVar("gme_run_report", default=None)
_active_stage: ContextVar[StageReport | None] = ContextVar("gme_run_report_stage", default=None)


def _code(value: Any, default: str | None = None) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else default


@contextmanager
def report_scope(enabled: bool) -> Iterator[ReportRecorder | None]:  # noqa: FBT001 - public opt-in context API
    recorder = ReportRecorder() if enabled else None
    token = _recorder.set(recorder)
    stage_token = _active_stage.set(None)
    try:
        yield recorder
    finally:
        _active_stage.reset(stage_token)
        _recorder.reset(token)


def start_report(run_id: str, runtime: str) -> None:
    recorder = _recorder.get()
    if recorder is not None:
        recorder.run_id = run_id
        recorder.runtime = runtime if runtime in {"llm", "hybrid", "rule_based"} else "unknown"


def _entities(value: Any) -> Iterator[dict[str, Any]]:  # noqa: C901 - explicit live-container boundaries
    if isinstance(value, BaseModel):
        # AgentResult.data is the live payload; raw_output and diagnostics are not.
        if "data" in type(value).model_fields:
            yield from _entities(value.data)
        else:
            for name in type(value).model_fields:
                if not name.startswith("_") and name != "excluded_entities":
                    yield from _entities(getattr(value, name))
    elif isinstance(value, dict):
        if value.get("id") or value.get("@id"):
            yield value
        else:
            for key, item in value.items():
                if not str(key).startswith("_") and key not in {"excluded_entities", "shacl", "shacl_report"}:
                    yield from _entities(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _entities(item)


def _snapshot(snapshot: Callable[[], Any]) -> dict[str, dict[str, str]]:
    return {
        str(entity.get("@id") or entity.get("id")): {
            key: sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
            for key, value in entity.items()
            if isinstance(key, str) and _PROPERTY.fullmatch(key)
        }
        for entity in _entities(snapshot())
    }


def _capture_snapshot(
    snapshot: Callable[[], Any] | None, recorder: ReportRecorder,
) -> dict[str, dict[str, str]] | None:
    if snapshot is None:
        return None
    try:
        return _snapshot(snapshot)
    except Exception:  # noqa: BLE001 - optional observation must not change execution
        limitation = "Some stage change snapshots were unavailable."
        if limitation not in recorder.limitations:
            recorder.limitations.append(limitation)
        return None


def _changes(before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]) -> ChangeSummary:
    added, removed = after.keys() - before.keys(), before.keys() - after.keys()
    properties_added = {prop for entity in added for prop in after[entity]}
    properties_removed = {prop for entity in removed for prop in before[entity]}
    properties_changed: set[str] = set()
    changed = 0
    for entity in before.keys() & after.keys():
        old, new = before[entity], after[entity]
        changed += old != new
        properties_added.update(new.keys() - old.keys())
        properties_removed.update(old.keys() - new.keys())
        properties_changed.update(prop for prop in old.keys() & new.keys() if old[prop] != new[prop])
    return ChangeSummary(
        entities_added=len(added), entities_removed=len(removed), entities_changed=changed,
        properties_added=sorted(properties_added), properties_removed=sorted(properties_removed),
        properties_changed=sorted(properties_changed),
    )


@contextmanager
def observe_stage(
    name: str, *, phase: StagePhase, snapshot: Callable[[], Any] | None = None,
) -> Iterator[StageReport | None]:
    recorder = _recorder.get()
    if recorder is None:
        yield None
        return
    stage = StageReport(name=name, phase=phase, started_at=datetime.now(timezone.utc))
    recorder.stages.append(stage)
    trace = get_trace()
    previous_ids = set(trace.activities) if trace else set()
    before = _capture_snapshot(snapshot, recorder)
    started = perf_counter()
    stage_token = _active_stage.set(stage)
    try:
        yield stage
    except BaseException as exc:
        stage.status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        stage.error_type = _code(stage.error_type) or _code(type(exc).__name__, "Exception")
        raise
    finally:
        _active_stage.reset(stage_token)
        stage.elapsed_ms = round((perf_counter() - started) * 1000, 3)
        stage.ended_at = datetime.now(timezone.utc)
        stage.activity_ids = [identifier for identifier in trace.activities if identifier not in previous_ids] if trace else []
        if before is not None:
            after = _capture_snapshot(snapshot, recorder)
            if after is not None:
                stage.changes = _changes(before, after)


def record_stage_outcome(status: StageStatus, *, reason: str | None = None) -> None:
    """Record a structured adapter outcome even when execution returns normally."""
    stage = _active_stage.get()
    if _recorder.get() is not None and stage is not None:
        stage.status = status
        stage.reason = _code(reason)


def record_skipped(name: str, *, phase: StagePhase, reason: str) -> None:
    recorder = _recorder.get()
    if recorder is not None:
        now = datetime.now(timezone.utc)
        recorder.stages.append(StageReport(name=name, phase=phase, status="skipped", reason=_code(reason), started_at=now, ended_at=now))


def record_validation(  # noqa: PLR0913 - explicit public validation outcome contract
    name: str, *, status: ValidationStatus, checked: int | None = None,
    violations: int | None = None, warnings: int = 0, reason: str | None = None,
) -> None:
    recorder = _recorder.get()
    if recorder is not None:
        recorder.validations.append(ValidationReport(
            name=name, status=status, checked=checked, violations=violations,
            warnings=warnings, reason=_code(reason),
        ))


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo is not None else None


def _timing(node: dict[str, Any]) -> dict[str, Any]:
    started, ended = _timestamp(node.get("started")), _timestamp(node.get("ended"))
    elapsed = max(0, (ended - started).total_seconds() * 1000) if started and ended else None
    return {"started_at": started, "ended_at": ended, "elapsed_ms": elapsed}


def _activity(identifier: str, node: dict[str, Any], run_id: str) -> ActivityReport:
    return ActivityReport(
        activity_id=identifier, name=_code(node.get("name"), "unknown"),
        kind=_code(node.get("kind"), "unknown"), status=_code(node.get("status"), "unknown"),
        run_id=_code(node.get("run_id")), parent_activity_id=_code(node.get("parent")),
        model=_code(node.get("model")), provider=_code(node.get("provider")),
        historical=node.get("run_id") != run_id, cache_hit=node.get("cache_hit") is True,
        **{key: _count(node.get(key)) for key in _USAGE_FIELDS},
        **{key: _code(node.get(key)) for key in ("mapping_version", "mapping_fingerprint", "source_contract", "target_contract")},
        **_timing(node),
    )


def _source_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    except ValueError:
        return None


def _retrieval(identifier: str, node: dict[str, Any], trace: EvidenceTrace, run_id: str) -> RetrievalReport:
    parent = trace.activities.get(node.get("activity"), {})
    current = parent.get("run_id") == run_id or identifier.startswith(f"urn:pulse:run:{run_id}:query:")
    return RetrievalReport(
        query_id=identifier, activity_id=_code(node.get("activity")),
        service=_code(node.get("service"), "unknown"), status=_code(node.get("status"), "unknown"),
        cache_hit=node.get("cache_hit") is True, historical=not current,
        sources=sorted({url for value in node.get("sources", []) or [] if (url := _source_url(value))}),
        **_timing(node),
    )


def _usage(activities: list[ActivityReport], *, evidence_available: bool) -> UsageSummary:
    models = [node for node in activities if node.kind == "model" and not node.historical and not node.cache_hit]
    missing = sum(any(getattr(node, key) is None for key in _USAGE_FIELDS) for node in models)
    return UsageSummary(
        model_calls=len(models), missing_usage_activities=missing,
        complete=evidence_available and not missing,
        **{key: sum(getattr(node, key) or 0 for node in models) for key in _USAGE_FIELDS},
    )


def _attribution(trace: EvidenceTrace, run_id: str) -> AttributionSummary:
    summary = AttributionSummary()
    for claim in trace.claims.values():
        summary.recorded_claims += 1
        if claim.get("query"):
            summary.cited_claims += 1
        elif claim.get("matches"):
            summary.matching_only_claims += 1
        else:
            summary.uncited_claims += 1
        if trace.activities.get(claim.get("activity"), {}).get("run_id") != run_id:
            summary.historical_claims += 1
    return summary


def build_run_report(
    *, output_run_id: str | None, status: ReportStatus, warning_count: int = 0,
) -> RunReport | None:
    recorder = _recorder.get()
    if recorder is None or recorder.run_id is None:
        return None
    report = RunReport(
        request_run_id=recorder.run_id, output_run_id=output_run_id,
        runtime=recorder.runtime, status=status,
        elapsed_ms=round((perf_counter() - recorder.started) * 1000, 3),
        warning_count=warning_count, limitations=[*_LIMITATIONS, *recorder.limitations],
    )
    if status == "cache_hit":
        report.limitations.append("Pipeline output was reused; this request did not execute extraction stages.")
        return report
    try:
        report.stages = [stage.model_copy(deep=True, update={"reason": _code(stage.reason), "error_type": _code(stage.error_type)}) for stage in recorder.stages]
        report.validations = [validation.model_copy(deep=True) for validation in recorder.validations]
        trace = get_trace()
        if trace is not None:
            report.activities = [_activity(identifier, node, recorder.run_id) for identifier, node in trace.activities.items()]
            report.usage = _usage(report.activities, evidence_available=True)
            report.retrievals = [_retrieval(identifier, node, trace, recorder.run_id) for identifier, node in trace.queries.items()]
            report.attribution = _attribution(trace, recorder.run_id)
        else:
            report.usage.complete = False
            report.limitations.append("Execution evidence was unavailable; model and retrieval coverage is unknown.")
    except Exception:  # noqa: BLE001 - incomplete diagnostics must not mask the extraction outcome
        report.usage.complete = False
        report.limitations.append("Execution evidence was incomplete; some report details could not be summarized.")
    return report
