"""The optional report describes work without leaking payloads or inventing usage."""
# ruff: noqa: PLR2004, EM101, TRY003, SIM117
# Literal expected counters/messages make the report's safety contract explicit.
from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest

from git_metadata_extractor.agents.models import AgentResult
from git_metadata_extractor.observation import run_report as report_observation
from git_metadata_extractor.observation.evidence import activity
from git_metadata_extractor.observation.query_log import QueryLog, query_log_var
from git_metadata_extractor.observation.run_report import (
    build_run_report,
    observe_stage,
    record_skipped,
    record_validation,
    report_scope,
    start_report,
)
from git_metadata_extractor.pipeline.runner import StageError


@contextmanager
def extraction(run_id="current", runtime="llm"):
    log = QueryLog(run_id, "example/repo")
    token = query_log_var.set(log)
    try:
        with report_scope(enabled=True):
            start_report(run_id, runtime)
            yield log.evidence
    finally:
        query_log_var.reset(token)


def test_disabled_report_never_calls_snapshots():
    def forbidden():
        pytest.fail("disabled reports must not inspect entity payloads")

    with report_scope(enabled=False):
        start_report("ignored", "rule_based")
        with observe_stage("generate", phase="generation", snapshot=forbidden) as stage:
            assert stage is None
        record_skipped("skip", phase="generation", reason="runtime_gate")
        record_validation("strict", status="passed")
        assert build_run_report(output_run_id="ignored", status="completed") is None


def test_stage_changes_count_entities_and_properties_without_raw_values():
    data = [AgentResult(data={"id": "a", "schema:name": "SECRET before", "schema:url": "removed", "_private": "old"})]
    with extraction(runtime="rule_based"):
        with observe_stage("reconcile", phase="post_processing", snapshot=lambda: data):
            data[0].data.update({"schema:name": "SECRET after", "schema:description": "SECRET body", "_private": "new", "identifiers": {"uuid": "changed"}})
            del data[0].data["schema:url"]
            data.append(AgentResult(data={"id": "b", "schema:name": "SECRET added"}))
        with observe_stage("reconcile", phase="post_processing", snapshot=lambda: data):
            data.pop()
        report = build_run_report(output_run_id="current", status="completed")
    assert [s.name for s in report.stages] == ["reconcile", "reconcile"]
    changes = report.stages[0].changes
    assert (changes.entities_added, changes.entities_removed, changes.entities_changed) == (1, 0, 1)
    assert changes.properties_added == ["schema:description", "schema:name"]
    assert changes.properties_changed == ["schema:name"]
    assert changes.properties_removed == ["schema:url"]
    assert report.stages[1].changes.entities_removed == 1
    assert "SECRET" not in report.model_dump_json()
    assert "_private" not in report.model_dump_json()
    assert "identifiers" not in report.model_dump_json()


def test_partial_skipped_failed_and_cancelled_stages_retain_outcomes():
    with extraction():
        with observe_stage("fanout", phase="generation") as partial:
            partial.status = "partial"
            partial.reason = "agent_failures"
        record_skipped("empty", phase="generation", reason="no_work")
        with pytest.raises(ValueError, match="secret exception"):
            with observe_stage("broken", phase="post_processing"):
                raise ValueError("secret exception")
        with pytest.raises(asyncio.CancelledError):
            with observe_stage("cancelled", phase="generation"):
                raise asyncio.CancelledError
        report = build_run_report(output_run_id="current", status="failed")
    assert [s.status for s in report.stages] == ["partial", "skipped", "failed", "cancelled"]
    assert report.stages[0].reason == "agent_failures"
    assert report.stages[2].error_type == "ValueError"
    assert "secret exception" not in report.model_dump_json()


def test_usage_counts_only_current_model_calls_and_marks_missing_usage():
    with extraction() as trace:
        trace.activities["old-model"] = {"kind": "model", "run_id": "old", "tokens_prompt": 900, "tokens_completion": 800}
        with observe_stage("agents", phase="generation"):
            with activity("agent", kind="llm") as agent:
                agent.update(tokens_prompt=100, tokens_completion=100)
                with activity("model", kind="model") as model:
                    model.update(model="example-model", provider="example", tokens_prompt=12, tokens_completion=4, requests=1, tool_calls=2)
                with activity("incomplete-model", kind="model") as missing:
                    missing.update(status="failed", tokens_prompt=3)
        report = build_run_report(output_run_id="current", status="completed")
    assert report.usage.tokens_prompt == 15
    assert report.usage.tokens_completion == 4
    assert report.usage.requests == 1
    assert report.usage.tool_calls == 2
    assert report.usage.model_calls == 2
    assert report.usage.missing_usage_activities == 1
    assert report.usage.complete is False
    assert len(report.stages[0].activity_ids) == 3
    assert "old-model" not in report.stages[0].activity_ids
    assert next(a for a in report.activities if a.activity_id == "old-model").historical is True


def test_retrievals_remove_userinfo_query_fragment_and_raw_evidence_fields():
    with extraction() as trace:
        with activity("agent", kind="rule_based"):
            query = trace.start_query("github.get_repo", {"token": "SECRET argument"})
            query.update(status="success", cache_hit=True, sources=["https://user:SECRET@github.com/org/repo?token=SECRET#SECRET"], body="SECRET body", error="SECRET error")
        report = build_run_report(output_run_id="current", status="completed")
    assert report.retrievals[0].sources == ["https://github.com/org/repo"]
    assert report.retrievals[0].cache_hit is True
    assert "SECRET" not in report.model_dump_json()
    assert "arguments" not in report.model_dump_json()


def test_cache_hit_never_claims_execution_or_model_usage():
    with extraction() as trace:
        trace.activities["old"] = {"kind": "model", "run_id": "old", "tokens_prompt": 900}
        with observe_stage("should_not_be_claimed", phase="generation"):
            trace.start_query("github", {})
        record_validation("strict", status="passed", checked=2)
        report = build_run_report(output_run_id="original", status="cache_hit")
    assert report.request_run_id == "current"
    assert report.output_run_id == "original"
    assert report.status == "cache_hit"
    assert report.stages == report.activities == report.retrievals == report.validations == []
    assert report.usage.model_calls == 0
    assert report.usage.tokens_prompt == 0


def test_validation_conformance_is_independent_of_stage_completion():
    with extraction():
        with observe_stage("shacl", phase="post_processing"):
            record_validation("output_shacl", status="violations", checked=3, violations=2, warnings=1)
        record_validation("substrate_shacl", status="unavailable", reason="validator_unavailable")
        report = build_run_report(output_run_id="current", status="completed", warning_count=1)
    assert report.stages[0].status == "completed"
    assert report.validations[0].status == "violations"
    assert report.validations[0].violations == 2
    assert report.validations[1].checked is None
    assert report.warning_count == 1


def test_concurrent_reports_and_nested_disabled_scopes_are_isolated():
    async def run(identifier):
        with extraction(identifier, "rule_based"):
            with observe_stage(identifier, phase="generation"):
                await asyncio.sleep(0)
                with report_scope(enabled=False):
                    record_skipped("hidden", phase="generation", reason="no_work")
            return build_run_report(output_run_id=identifier, status="completed")

    async def both():
        return await asyncio.gather(run("one"), run("two"))

    first, second = asyncio.run(both())
    assert first.request_run_id == "one"
    assert [s.name for s in first.stages] == ["one"]
    assert second.request_run_id == "two"
    assert [s.name for s in second.stages] == ["two"]
    assert build_run_report(output_run_id="outside", status="completed") is None


def test_snapshot_failure_cannot_change_extraction_outcome_or_mask_error():
    def unavailable():
        raise ValueError("SECRET payload access")

    with extraction():
        with observe_stage("snapshot_unavailable", phase="generation", snapshot=unavailable):
            pass
        with pytest.raises(RuntimeError, match="original"):
            with observe_stage("failed", phase="generation", snapshot=unavailable):
                raise RuntimeError("original")
        report = build_run_report(output_run_id="current", status="failed")
    assert [s.status for s in report.stages] == ["completed", "failed"]
    assert all(s.changes is None for s in report.stages)
    assert any("snapshot" in text.lower() for text in report.limitations)
    assert "SECRET" not in report.model_dump_json()


def test_missing_evidence_usage_and_cached_models_are_not_fabricated():
    with extraction() as trace:
        trace.activities["cached"] = {"kind": "model", "run_id": "current", "cache_hit": True, "tokens_prompt": 100}
        trace.activities["partial"] = {"kind": "model", "run_id": "current"}
        trace.queries["partial-query"] = {"activity": "partial"}
        report = build_run_report(output_run_id="current", status="failed")
    assert report.usage.model_calls == 1
    assert report.usage.tokens_prompt == 0
    assert report.usage.complete is False
    assert report.usage.missing_usage_activities == 1
    assert report.retrievals[0].status == "unknown"

    with report_scope(enabled=True):
        start_report("no-evidence", "llm")
        report = build_run_report(output_run_id=None, status="failed")
    assert report.usage.complete is False


def test_helper_fields_and_shacl_diagnostics_do_not_change_entity_summary():
    data = {"entities": [{"id": "a", "schema:name": "name", "@type": "schema:Person", "identifiers": {"uuid": "before"}}]}
    with extraction():
        with observe_stage("validate", phase="post_processing", snapshot=lambda: data):
            data["entities"][0].update({"@type": "type", "identifiers": {"uuid": "after"}, "_helper": "SECRET"})
            data["shacl_report"] = {"id": "diagnostic", "schema:name": "SECRET"}
            data["excluded_entities"] = [{"id": "excluded", "schema:name": "SECRET"}]
        report = build_run_report(output_run_id="current", status="completed")
    assert report.stages[0].changes.entities_changed == 0
    assert report.stages[0].changes.entities_added == 0


def test_timeline_and_mapping_metadata_describe_observed_execution():
    with extraction(runtime="rule_based") as trace:
        with observe_stage("mapping", phase="generation"):
            trace.activities["mapping"] = {
                "run_id": "current", "kind": "mapping", "name": "github.person",
                "started": "2026-11-11T10:00:00+00:00", "ended": "2026-11-11T10:00:00.250+00:00",
                "mapping_version": "1.2", "mapping_fingerprint": "abc123",
                "source_contract": "github.rest.user", "target_contract": "agent.person",
            }
            trace.queries["query"] = {
                "activity": "mapping", "started": "2026-11-11T10:00:00+00:00",
                "ended": "2026-11-11T10:00:00.125+00:00",
            }
        record_skipped("gated", phase="post_processing", reason="runtime_gate")
        report = build_run_report(output_run_id="current", status="completed")
    assert report.activities[0].elapsed_ms == 250
    assert report.activities[0].started_at.isoformat() == "2026-11-11T10:00:00+00:00"
    assert report.activities[0].mapping_version == "1.2"
    assert report.activities[0].mapping_fingerprint == "abc123"
    assert report.activities[0].source_contract == "github.rest.user"
    assert report.activities[0].target_contract == "agent.person"
    assert report.retrievals[0].elapsed_ms == 125
    assert report.stages[0].started_at <= report.stages[0].ended_at
    assert report.stages[1].started_at == report.stages[1].ended_at


def test_malformed_partial_evidence_cannot_break_a_domain_failure_report():
    with extraction() as trace:
        with observe_stage("generation", phase="generation") as stage:
            stage.status = "partial"
        trace.queries["broken"] = {"sources": 42, "error": "SECRET detail"}
        report = build_run_report(output_run_id="current", status="failed")
    assert report.status == "failed"
    assert report.stages[0].status == "partial"
    assert report.usage.complete is False
    assert any("incomplete" in item.lower() for item in report.limitations)
    assert "SECRET" not in report.model_dump_json()


@pytest.mark.parametrize("outcome", ["partial", "failed", "skipped"])
def test_structured_stage_outcomes_survive_normal_return(outcome):
    with extraction():
        with observe_stage("adapter", phase="post_processing"):
            report_observation.record_stage_outcome(outcome, reason="structured_failure")
        report = build_run_report(output_run_id="current", status="completed")
    assert report.stages[0].status == outcome
    assert report.stages[0].reason == "structured_failure"
    assert report.stages[0].error_type is None


def test_nested_stage_outcomes_restore_parent_and_finished_stages_stay_unchanged():
    with extraction():
        with observe_stage("parent", phase="post_processing"):
            with observe_stage("child", phase="generation"):
                report_observation.record_stage_outcome("skipped", reason="no_work")
            report_observation.record_stage_outcome("partial", reason="agent_failures")
        report_observation.record_stage_outcome("failed", reason="outside_stage")
        report = build_run_report(output_run_id="current", status="completed")
    assert [(stage.name, stage.status, stage.reason) for stage in report.stages] == [
        ("parent", "partial", "agent_failures"),
        ("child", "skipped", "no_work"),
    ]


def test_report_scopes_isolate_outcomes_and_restore_outer_stage():
    with extraction():
        with observe_stage("outer", phase="generation") as outer:
            with report_scope(enabled=False):
                report_observation.record_stage_outcome("failed", reason="disabled")
                with observe_stage("disabled", phase="generation"):
                    report_observation.record_stage_outcome("failed", reason="disabled")
            assert outer.status == "completed"
            with report_scope(enabled=True):
                start_report("nested", "rule_based")
                report_observation.record_stage_outcome("failed", reason="no_nested_stage")
                with observe_stage("nested", phase="generation"):
                    report_observation.record_stage_outcome("skipped", reason="no_work")
                nested_report = build_run_report(output_run_id="nested", status="completed")
            assert outer.status == "completed"
            report_observation.record_stage_outcome("partial", reason="outer_failure")
        report = build_run_report(output_run_id="current", status="completed")
    assert len(report.stages) == 1
    assert report.stages[0].status == "partial"
    assert nested_report.stages[0].status == "skipped"


def test_stage_outcome_context_restores_parent_after_child_failure():
    def failing_child():
        with observe_stage("child", phase="generation"):
            report_observation.record_stage_outcome("partial", reason="safe_code")
            raise ValueError("failure")

    with extraction():
        with observe_stage("parent", phase="generation"):
            with pytest.raises(ValueError, match="failure"):
                failing_child()
            report_observation.record_stage_outcome("partial", reason="caught_child_failure")
        report = build_run_report(output_run_id="current", status="completed")
    assert report.stages[0].status == "partial"
    assert report.stages[1].status == "failed"
    assert report.stages[1].reason == "safe_code"
    assert report.stages[1].error_type == "ValueError"


@pytest.mark.parametrize(("explicit_type", "expected"), [("RuntimeError", "RuntimeError"), ("SECRET error message", "StageError")])
def test_wrapper_exception_preserves_safe_underlying_error_type(explicit_type, expected):
    def failing_adapter():
        with observe_stage("fatal", phase="post_processing") as stage:
            stage.error_type = explicit_type
            raise StageError("fatal", RuntimeError("SECRET cause"))

    with extraction():
        with pytest.raises(StageError):
            failing_adapter()
        report = build_run_report(output_run_id="current", status="failed")
    assert report.stages[0].status == "failed"
    assert report.stages[0].error_type == expected
    assert "SECRET" not in report.model_dump_json()
