# ruff: noqa: SLF001
# Exercise the adapters used by the production stage chains.
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from rdflib import Graph

from git_metadata_extractor import config, validation
from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.agents.models import AgentResult
from git_metadata_extractor.observation.evidence import activity
from git_metadata_extractor.observation.query_log import QueryLog, query_log_var
from git_metadata_extractor.observation.run_report import (
    build_run_report,
    report_scope,
    start_report,
)
from git_metadata_extractor.pipeline import (
    PipelineOrchestrator,
    run,
    runner,
    settings,
    stages,
)
from git_metadata_extractor.pipeline.runner import Stage, StageError, run_pipeline
from git_metadata_extractor.pipeline.stages.link_veracity import LinkVeracityStageResult
from git_metadata_extractor.pipeline.stages.models import (
    AssembledOutput,
    ContextBundle,
    ReconciledEntities,
)
from git_metadata_extractor.pipeline.stages.refine_with_llm import RefineWithLLMResult
from git_metadata_extractor.pipeline.stages.resolve_bio_to_ror_llm import (
    BioLLMAffiliationResult,
)
from git_metadata_extractor.pipeline.state import PipelineState
from git_metadata_extractor.providers.detection.github_url_classifier import (
    classify_github_url,
)
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.validation.layers import Layer, LayerValidationResult
from git_metadata_extractor.validation.shacl_validation import (
    SHACLRuntimeUnavailableError,
    SHACLValidationResult,
)


def _state() -> PipelineState:
    return PipelineState(
        run_id="report-run",
        classification=classify_github_url("https://github.com/acme/widget", probe_accounts=False),
        runtime="rule_based",
        providers=None,
    )


def _report():
    report = build_run_report(output_run_id="report-run", status="completed")
    assert report is not None
    return report


def _boom(_state):
    message = "secret exception payload"
    raise ValueError(message)


def test_report_preserves_gates_failures_and_repeated_names():
    state = _state()
    with report_scope(enabled=True):
        start_report("report-run", "rule_based")
        asyncio.run(run_pipeline(state, [
            Stage(name="repeat", run=lambda _s: None, applies=lambda _s: False),
            Stage(name="bad_gate", run=lambda _s: None, applies=_boom),
            Stage(name="broken", run=_boom),
            Stage(name="repeat", run=lambda _s: None),
        ]))
        report = _report()
    assert [(stage.name, stage.status) for stage in report.stages] == [
        ("repeat", "skipped"), ("bad_gate", "failed"),
        ("broken", "failed"), ("repeat", "completed"),
    ]
    assert report.stages[0].reason == "gate_disabled"
    assert report.stages[1].reason == "gate_error"
    assert "secret exception payload" not in report.model_dump_json()
    assert [item.status for item in state.stage_outcomes] == [
        "skipped", "failed", "failed", "completed",
    ]


@pytest.mark.parametrize("cancel", [False, True])
def test_report_retains_fatal_and_cancelled_stage(cancel):
    def fail(_state):
        if cancel:
            raise asyncio.CancelledError
        _boom(_state)

    with report_scope(enabled=True):
        start_report("report-run", "rule_based")
        with pytest.raises(asyncio.CancelledError if cancel else StageError):
            asyncio.run(run_pipeline(_state(), [Stage(name="fatal", run=fail, fail_open=False)]))
        assert _report().stages[0].status == ("cancelled" if cancel else "failed")


def test_disabled_report_does_not_snapshot(monkeypatch):
    def forbidden(_state):
        pytest.fail("snapshot must not execute when reporting and evidence are disabled")

    monkeypatch.setattr(runner, "state_entities", forbidden)
    monkeypatch.setattr(runner, "get_trace", lambda: None)
    with report_scope(enabled=False):
        asyncio.run(run_pipeline(_state(), [Stage(name="noop", run=lambda _s: None)]))
        assert build_run_report(output_run_id=None, status="completed") is None


def test_strict_report_counts_invalid_entities_without_claiming_stage_failure():
    state = _state()
    state.reconciled = ReconciledEntities(entities={"persons": [{"id": "invalid"}]})
    with report_scope(enabled=True):
        start_report("report-run", "rule_based")
        asyncio.run(run_pipeline(state, [Stage(name="strict_validation", run=run._strict_validation)]))
        report = _report()
    assert report.stages[0].status == "completed"
    validation = report.validations[0]
    assert validation.name == "strict_validation"
    assert validation.status == "violations"
    assert validation.checked == 1
    assert validation.violations == 1


@pytest.mark.parametrize("outcome", ["passed", "violations", "unavailable", "error", "parse_error"])
def test_shacl_reports_conformance_separately_from_execution(monkeypatch, outcome):
    monkeypatch.setattr(validation, "load_ontology_shapes_graph", Graph)
    monkeypatch.setattr(settings, "jsonld_to_graph", lambda _payload: None if outcome == "parse_error" else Graph())

    def validate(*_args):
        if outcome == "unavailable":
            message = "secret unavailable detail"
            raise SHACLRuntimeUnavailableError(message)
        if outcome == "error":
            message = "secret validation detail"
            raise ValueError(message)
        return SHACLValidationResult(
            conforms=outcome == "passed",
            violations=[{"message": "secret value"}] if outcome == "violations" else [],
        )

    monkeypatch.setattr(validation.SHACLValidator, "validate_graph", validate)
    with report_scope(enabled=True):
        start_report("report-run", "rule_based")
        asyncio.run(run_pipeline(_state(), [Stage(name="shacl_gate", run=run._shacl_gate)]))
        report = _report()
    assert report.stages[0].status == "completed"
    assert report.validations[0].status == ("error" if outcome == "parse_error" else outcome)
    assert "secret" not in report.model_dump_json()


@pytest.mark.parametrize(("partial", "errors", "expected"), [
    (True, [], "partial"), (False, ["private error"], "partial"), (False, [], "completed"),
])
def test_generation_observes_partial_results_and_no_work(monkeypatch, partial, errors, expected):
    orchestrator = PipelineOrchestrator()
    plan = orchestrator.get_execution_plan("repository")
    plan.stages = [stage for stage in plan.stages if stage.name in {"repo_agent", "person_agents"}]
    monkeypatch.setattr(orchestrator, "_build_work_items", lambda name, *_args: [object()] if name == "repo_agent" else [])

    async def execute_stage(**_kwargs):
        return {"repo_agent": AgentResult(data={"id": "https://github.com/acme/widget"}, is_partial=partial)}, [], errors

    monkeypatch.setattr(orchestrator, "_execute_stage", execute_stage)
    with report_scope(enabled=True):
        start_report("report-run", "rule_based")
        result = asyncio.run(orchestrator.execute(plan, ProviderSet(github=MockGitHubProvider()), {"agent_runtime": "rule_based"}))
        report = _report()
    assert [(stage.name, stage.status) for stage in report.stages] == [
        ("repo_agent", expected), ("person_agents", "skipped"),
    ]
    assert report.stages[-1].reason == "no_work_items"
    assert result.stages_completed == ["repo_agent", "person_agents"]
    assert "private error" not in report.model_dump_json()


@pytest.mark.parametrize("skipped", [False, True])
def test_substrate_report_distinguishes_unavailable_from_violations(monkeypatch, skipped):
    monkeypatch.setattr(config, "substrate_validate", lambda: True)
    monkeypatch.setattr(validation, "validate_substrate", lambda _document: LayerValidationResult(
        layer=Layer.SUBSTRATE, conforms=skipped, skipped=skipped,
        reason="private unavailable reason" if skipped else None,
        violations=[] if skipped else [{"message": "private violation value"}],
    ))
    state = _state()
    state.read_only = True
    state.substrate = {"@context": {}, "@graph": []}
    with report_scope(enabled=True):
        start_report("report-run", "rule_based")
        asyncio.run(run_pipeline(state, [Stage(name="substrate_write", run=run._write_substrate)]))
        report = _report()
    assert report.validations[0].status == ("unavailable" if skipped else "violations")
    assert "private" not in report.model_dump_json()


@pytest.mark.parametrize(("runtime", "expected"), [("rule_based", "skipped"), ("hybrid", "skipped"), ("llm", "partial")])
def test_context_summary_reports_skipping_and_fail_open(runtime, expected):
    async def failing_summary(**_kwargs):
        message = "private summary error"
        raise ValueError(message)

    orchestrator = PipelineOrchestrator(
        context_gatherer=lambda *_args: ContextBundle(detected_type="repository"),
        llm_context_summary_agent=SimpleNamespace(run=failing_summary),
        retry_max_retries=0,
        plans_by_type={"repository": ["context_gather"]},
    )
    with report_scope(enabled=True):
        start_report("report-run", runtime)
        asyncio.run(orchestrator.execute(
            orchestrator.get_execution_plan("repository"), ProviderSet(github=MockGitHubProvider()),
            {"agent_runtime": runtime, "url_info": _state().classification},
        ))
        report = _report()
    summary = next(stage for stage in report.stages if stage.name == "context_summary_agent")
    assert summary.status == expected
    assert "private" not in report.model_dump_json()


@pytest.mark.parametrize("runtime", ["rule_based", "llm"])
def test_generation_reports_no_results_failure_and_llm_root_abort(monkeypatch, runtime):
    orchestrator = PipelineOrchestrator(plans_by_type={"repository": ["repo_agent"]})
    monkeypatch.setattr(orchestrator, "_build_work_items", lambda *_args: [object()])

    async def fail(**_kwargs):
        return {}, [], ["private agent error"]

    monkeypatch.setattr(orchestrator, "_execute_stage", fail)
    with report_scope(enabled=True):
        start_report("report-run", runtime)
        execution = orchestrator.execute(
            orchestrator.get_execution_plan("repository"), ProviderSet(github=MockGitHubProvider()),
            {"agent_runtime": runtime},
        )
        if runtime == "llm":
            with pytest.raises(RuntimeError):
                asyncio.run(execution)
        else:
            assert asyncio.run(execution).errors == ["private agent error"]
        report = _report()
    assert report.stages[0].status == "failed"
    assert "private agent error" not in report.model_dump_json()


def test_stage_report_captures_mutation_without_payload_values():
    state = _state()
    state.buckets = {"persons": [{"id": "https://github.com/alice", "schema:name": "Before"}]}

    def rename(current):
        current.buckets["persons"][0]["schema:name"] = "Private after value"

    with report_scope(enabled=True):
        start_report("report-run", "rule_based")
        asyncio.run(run_pipeline(state, [Stage(name="rename", run=rename)]))
        report = _report()
    assert report.stages[0].changes.properties_changed == ["schema:name"]
    assert report.stages[0].changes.entities_changed == 1
    assert "Private after value" not in report.model_dump_json()


@pytest.mark.parametrize("runtime", ["rule_based", "hybrid", "llm"])
def test_production_link_veracity_gate_reports_skip(monkeypatch, runtime):
    monkeypatch.setattr(settings, "is_link_veracity_enabled", lambda: False)
    state = _state()
    state.runtime = runtime
    # No assembled output: executing the adapter would fail, whereas the
    # real runtime/flag gate must skip it before touching entities or providers.
    stages = [stage for stage in run.OUTPUT_CHAIN if stage.name == "link_veracity"]
    with report_scope(enabled=True):
        start_report("report-run", runtime)
        asyncio.run(run_pipeline(state, stages))
        report = _report()
    assert len(report.stages) == 1
    assert report.stages[0].status == "skipped"
    assert state.stage_outcomes[0].status == "skipped"
    assert not state.warnings


def test_generation_report_links_existing_activities_without_new_parent_scope(monkeypatch):
    orchestrator = PipelineOrchestrator(plans_by_type={"repository": ["repo_agent"]})
    monkeypatch.setattr(orchestrator, "_build_work_items", lambda *_args: [object()])
    log = QueryLog("report-run", "acme/widget")

    async def execute_stage(**_kwargs):
        assert log.evidence.active_id() is None
        with activity("existing_agent", kind="agent"):
            return {"repo_agent": AgentResult(data={"id": "repo"})}, [], []

    monkeypatch.setattr(orchestrator, "_execute_stage", execute_stage)
    token = query_log_var.set(log)
    try:
        with report_scope(enabled=True):
            start_report("report-run", "rule_based")
            asyncio.run(orchestrator.execute(
                orchestrator.get_execution_plan("repository"), ProviderSet(github=MockGitHubProvider()),
                {"agent_runtime": "rule_based"},
            ))
            report = _report()
    finally:
        query_log_var.reset(token)
    assert len(log.evidence.activities) == 1
    assert report.stages[0].activity_ids == list(log.evidence.activities)
    assert report.stages[0].changes.entities_added == 1


@pytest.mark.parametrize(("refined", "skipped", "failed", "by_type", "expected"), [
    (0, 0, 0, {}, "completed"),
    (0, 0, 1, {}, "failed"),
    (1, 0, 1, {}, "partial"),
    (0, 1, 1, {}, "partial"),
    (0, 0, 1, {"org_resolver": {"resolved": 1, "failed": 1}}, "partial"),
    (0, 0, 1, {"org_resolver": {"declined": 1, "failed": 1}}, "partial"),
    (0, 0, 1, {"rescue": {"rescued": 1}}, "partial"),
    (0, 0, 1, {"discovery": {"proposed": 1}}, "partial"),
])
def test_refiner_reports_structured_item_failures(monkeypatch, refined, skipped, failed, by_type, expected):  # noqa: PLR0913, PLR0917
    state = _state()
    state.reconciled = ReconciledEntities()
    result = RefineWithLLMResult(
        reconciled=state.reconciled, refined_count=refined, skipped_count=skipped,
        failed_count=failed, by_type=by_type,
        warnings=["private detail independent of status"],
    )

    async def refine(**_kwargs):
        return result

    monkeypatch.setattr(stages, "run_refine_with_llm_stage", refine)
    with report_scope(enabled=True):
        start_report("report-run", "hybrid")
        asyncio.run(run_pipeline(state, [Stage(name="refine_with_llm", run=run._refine_with_llm)]))
        report = _report()
    assert report.stages[0].status == expected
    assert state.stage_outcomes[0].status == "completed"
    assert state.warnings == result.warnings
    assert "private detail" not in report.model_dump_json()


@pytest.mark.parametrize(("called", "failed", "expected"), [
    (0, 0, "completed"), (1, 1, "failed"), (2, 1, "partial"), (1, 0, "completed"),
])
def test_bio_resolver_reports_failures_but_accepts_no_match(monkeypatch, called, failed, expected):
    state = _state()
    state.reconciled = ReconciledEntities()
    result = BioLLMAffiliationResult(
        persons_examined=3, persons_called=called, persons_resolved=0, persons_failed=failed,
    )

    async def resolve(**_kwargs):
        return result

    monkeypatch.setattr(run, "run_resolve_bio_to_ror_llm_stage", resolve)
    with report_scope(enabled=True):
        start_report("report-run", "hybrid")
        asyncio.run(run_pipeline(state, [Stage(name="resolve_bio_to_ror_llm", run=run._resolve_bio_to_ror_llm)]))
        report = _report()
    assert report.stages[0].status == expected
    assert state.stage_outcomes[0].status == "completed"


@pytest.mark.parametrize(("checked", "supported", "unsupported", "failed", "expected"), [
    (0, 0, 0, 0, "completed"),
    (1, 0, 0, 1, "failed"),
    (2, 1, 0, 1, "partial"),
    (2, 0, 1, 1, "partial"),
    (1, 0, 1, 0, "completed"),
])
def test_link_veracity_distinguishes_execution_failures_from_negative_verdicts(  # noqa: PLR0913, PLR0917
    monkeypatch, checked, supported, unsupported, failed, expected,
):
    state = _state()
    state.assembled = AssembledOutput(root_entity=None)
    result = LinkVeracityStageResult(
        checked_count=checked, supported_count=supported,
        unsupported_count=unsupported, failed_count=failed,
    )

    async def verify(**_kwargs):
        return result

    monkeypatch.setattr(stages, "run_link_veracity_stage", verify)
    with report_scope(enabled=True):
        start_report("report-run", "llm")
        asyncio.run(run_pipeline(state, [Stage(name="link_veracity", run=run._link_veracity)]))
        report = _report()
    assert report.stages[0].status == expected
    assert state.stage_outcomes[0].status == "completed"
