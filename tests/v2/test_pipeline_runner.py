from __future__ import annotations

import asyncio

import pytest

from git_metadata_extractor.pipeline.runner import (
    Stage,
    StageError,
    run_pipeline,
)
from git_metadata_extractor.pipeline.state import PipelineState


class _DetectedType:
    value = "repository"


class _Classification:
    detected_type = _DetectedType()
    normalized_url = "https://github.com/acme/widget"


def _state() -> PipelineState:
    return PipelineState(
        run_id="run-1",
        classification=_Classification(),
        runtime="rule_based",
        providers=None,
    )


def _run(stages: list[Stage], state: PipelineState | None = None) -> PipelineState:
    """No pytest-asyncio in this repo; drive the coroutine like the other tests."""
    return asyncio.run(run_pipeline(state or _state(), stages))


def test_stages_run_in_declared_order() -> None:
    seen: list[str] = []

    def make(name: str) -> Stage:
        def run(state: PipelineState) -> None:
            seen.append(name)
            state.extras.setdefault("order", []).append(name)

        return Stage(name=name, run=run)

    state = _run([make("a"), make("b"), make("c")])

    assert seen == ["a", "b", "c"]
    assert state.extras["order"] == ["a", "b", "c"]


def test_async_and_sync_stages_both_supported() -> None:
    async def async_stage(state: PipelineState) -> None:
        state.extras["async"] = True

    def sync_stage(state: PipelineState) -> None:
        state.extras["sync"] = True

    state = _run([Stage(name="a", run=async_stage), Stage(name="s", run=sync_stage)])

    assert state.extras == {"async": True, "sync": True}


def test_gate_skips_stage_without_running_it() -> None:
    def run(state: PipelineState) -> None:
        state.extras["ran"] = True

    state = _run([Stage(name="gated", run=run, applies=lambda _s: False)])

    assert "ran" not in state.extras
    assert state.extras["gated_skipped"] is True
    assert "gated" not in state.timings


def test_fail_open_stage_records_warning_and_continues() -> None:
    def boom(_state: PipelineState) -> None:
        message = "kaboom"
        raise ValueError(message)

    def after(state: PipelineState) -> None:
        state.extras["reached"] = True

    state = _run([Stage(name="broken", run=boom), Stage(name="after", run=after)])

    assert state.extras["reached"] is True
    assert state.warnings == ["broken stage failed: kaboom"]
    # A failed stage is still timed — otherwise a slow failure is invisible.
    assert "broken" in state.timings


def test_fail_closed_stage_aborts_the_run() -> None:
    """`reconcile_entities` and `assemble_output` must still produce a 500."""

    def boom(_state: PipelineState) -> None:
        message = "unrecoverable"
        raise ValueError(message)

    def never(state: PipelineState) -> None:  # pragma: no cover
        state.extras["reached"] = True

    with pytest.raises(StageError) as excinfo:
        _run(
            [
                Stage(name="critical", run=boom, fail_open=False),
                Stage(name="after", run=never),
            ],
        )

    assert excinfo.value.stage_name == "critical"
    assert isinstance(excinfo.value.cause, ValueError)


def test_broken_gate_skips_rather_than_aborting() -> None:
    def gate(_state: PipelineState) -> bool:
        message = "gate exploded"
        raise RuntimeError(message)

    def run(state: PipelineState) -> None:  # pragma: no cover
        state.extras["ran"] = True

    state = _run([Stage(name="g", run=run, applies=gate)])

    assert "ran" not in state.extras
    assert state.warnings == ["g gate failed; stage skipped"]


def test_warn_deduplicates_like_the_route_helper() -> None:
    def run(state: PipelineState) -> None:
        state.warn("same")
        state.warn("same")
        state.warn_all(["other", "other", ""])

    state = _run([Stage(name="w", run=run)])

    assert state.warnings == ["same", "other"]


def test_state_exposes_classification_shortcuts() -> None:
    state = _state()

    assert state.detected_type == "repository"
    assert state.source_url == "https://github.com/acme/widget"


def test_adapters_can_log_their_own_duration_mid_stage() -> None:
    """Regression: every moved stage logged `in 0.00s`.

    `state.timings[name]` is written by the runner only *after* the stage
    returns, but the adapters in `pipeline/run.py` log their per-stage line
    while still inside the stage. Reading `timings` there yielded 0.0 for all
    14 moved stages, quietly replacing the route's real per-stage durations
    with zeros. The runner now publishes `state.stage_started_at`.
    """
    import time  # noqa: PLC0415

    from git_metadata_extractor.pipeline.run import _elapsed  # noqa: PLC0415

    seen: list[float] = []
    slept = 0.05

    def stage(state: PipelineState) -> None:
        time.sleep(slept)
        assert isinstance(state.stage_started_at, float)
        seen.append(_elapsed(state))

    state = _state()
    asyncio.run(run_pipeline(state, [Stage("timed", stage, fail_open=False)]))

    assert seen, "stage did not run"
    assert seen[0] >= slept, f"adapter measured {seen[0]:.3f}s for a {slept}s stage"
    # And the runner's own recorded value still agrees.
    assert state.timings["timed"] >= slept
