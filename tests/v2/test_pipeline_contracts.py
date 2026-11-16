"""Pipeline control data has validated types, distinct from open entity payloads."""

import asyncio

import pytest
from pydantic import ValidationError

from git_metadata_extractor.pipeline.runner import Stage, StageError, run_pipeline
from git_metadata_extractor.pipeline.state import PipelineState
from git_metadata_extractor.providers.detection.models import GitHubURLClassification


def make_state():
    return PipelineState(
        run_id="test-run",
        classification=GitHubURLClassification(
            normalized_url="https://github.com/acme/tool",
            detected_type="repository",
            owner="acme",
            repo="tool",
        ),
        runtime="rule_based",
        providers=None,
    )


def test_pipeline_control_fields_validate_construction_and_assignment():
    state = make_state()
    with pytest.raises(ValidationError):
        state.classification = object()
    with pytest.raises(ValidationError):
        state.max_concurrent_agents = 0
    with pytest.raises(ValidationError):
        state.artifacts.link_veracity_seconds = -1
    with pytest.raises(ValidationError):
        state.artifacts.misspelled_stage_result = True


def test_required_gate_failure_aborts_and_records_outcome():
    state = make_state()

    def broken_gate(_state):
        raise ValueError

    stage = Stage(
        name="required", run=lambda _state: None, applies=broken_gate, fail_open=False,
    )
    with pytest.raises(StageError):
        asyncio.run(run_pipeline(state, [stage]))
    assert state.stage_outcomes[-1].status == "failed"
    assert state.stage_outcomes[-1].phase == "gate"


def test_stage_outcomes_preserve_skips_and_repeated_stage_names():
    state = make_state()
    asyncio.run(
        run_pipeline(
            state,
            [
                Stage(name="same", run=lambda _state: None),
                Stage(
                    name="same", run=lambda _state: None, applies=lambda _state: False,
                ),
            ],
        ),
    )
    assert [item.status for item in state.stage_outcomes] == ["completed", "skipped"]
    assert all(item.name == "same" for item in state.stage_outcomes)


def test_cancelled_stage_is_recorded_and_cancellation_propagates():
    state = make_state()

    async def cancel(_state):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_pipeline(state, [Stage(name="cancelled", run=cancel)]))
    assert state.stage_outcomes[-1].status == "cancelled"
