"""Run named post-agent stages against a shared PipelineState.

Each stage declares a callable, an applicability gate and a failure policy.
Fail-open stages append warnings; required stages raise StageError. The runner
records timing and provenance without changing the declared stage order.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from time import perf_counter
from typing import Awaitable, Callable

from pydantic import Field

from git_metadata_extractor.models import InternalModel
from git_metadata_extractor.observation.evidence import (
    activity,
    get_trace,
    state_entities,
)
from git_metadata_extractor.pipeline.state import PipelineState, StageOutcome

logger = logging.getLogger(__name__)

# These aliases are evaluated by Pydantic when constructing Stage validators.
StageFn = Callable[[PipelineState], Awaitable[None] | None]
StageGate = Callable[[PipelineState], bool]


def _always(_state: PipelineState) -> bool:
    return True


class Stage(InternalModel):
    """A named, gated step that mutates the pipeline state in place."""

    name: str
    run: StageFn
    applies: StageGate = Field(default=_always)
    fail_open: bool = True


class StageError(RuntimeError):
    """A stage that does not fail open raised. The route turns this into 500."""

    def __init__(self, stage_name: str, cause: BaseException) -> None:
        super().__init__(f"{stage_name} stage failed: {cause}")
        self.stage_name = stage_name
        self.cause = cause


async def _invoke(stage: Stage, state: PipelineState) -> None:
    result = stage.run(state)
    if inspect.isawaitable(result):
        await result


async def run_pipeline(state: PipelineState, stages: list[Stage]) -> PipelineState:
    """Run `stages` in order against `state`, timing and gating each one.

    Stages mutate `state` rather than returning a new one: the sequence
    threads four payloads plus several accumulators, and returning a fresh
    model per step would mean every stage restating the fields it did not
    touch.
    """
    for stage in stages:
        try:
            if not stage.applies(state):
                state.stage_outcomes.append(StageOutcome(name=stage.name, status="skipped", phase="gate"))
                logger.debug("%s: skipped by gate", stage.name)
                continue
        except Exception as exc:
            state.stage_outcomes.append(StageOutcome(
                name=stage.name, status="failed", phase="gate", error_type=type(exc).__name__,
            ))
            if not stage.fail_open:
                raise StageError(stage.name, exc) from exc
            logger.exception("%s: optional gate failed; stage skipped", stage.name)
            state.warn(f"{stage.name} gate failed; stage skipped")
            continue

        started_at = perf_counter()
        # Published so an adapter can log its own duration *before* returning.
        # The authoritative value below is only written once the stage is done,
        # so an adapter reading `state.timings` mid-stage always saw 0.00s.
        state.stage_started_at = started_at
        try:
            with activity(stage.name, kind="transformation"):
                await _invoke(stage, state)
                trace = get_trace()
                if trace is not None:
                    trace.observe(state_entities(state))
        except asyncio.CancelledError:
            elapsed = perf_counter() - started_at
            state.timings[stage.name] = elapsed
            state.stage_outcomes.append(StageOutcome(
                name=stage.name, status="cancelled", duration_seconds=elapsed,
            ))
            raise
        except Exception as exc:
            elapsed = perf_counter() - started_at
            state.timings[stage.name] = elapsed
            state.stage_outcomes.append(StageOutcome(
                name=stage.name, status="failed", duration_seconds=elapsed,
                error_type=type(exc).__name__,
            ))
            if not stage.fail_open:
                logger.exception("%s stage failed (fatal)", stage.name)
                raise StageError(stage.name, exc) from exc
            logger.exception("%s stage failed", stage.name)
            state.warn(f"{stage.name} stage failed: {exc}")
        else:
            elapsed = perf_counter() - started_at
            state.timings[stage.name] = elapsed
            state.stage_outcomes.append(StageOutcome(
                name=stage.name, status="completed", duration_seconds=elapsed,
            ))

    return state
