"""One signature for every post-agent stage, and the loop that runs them.

`api/extract.py` used to inline all 33 stage calls, each with its own
`perf_counter()` bracket, its own `try/except` and its own warning plumbing.
The shapes were nearly identical but never quite, so the sequence could not be
reordered, tested in isolation, or read as a list.

A stage here is a name, a callable that mutates `PipelineState`, an optional
gate, and — importantly — a declaration of whether it fails open.

    Stage(name="reconcile_entities", run=_reconcile, fail_open=False)

`fail_open` is not cosmetic. Today most stages swallow exceptions and append a
warning, but a few (`reconcile_entities`, `assemble_output`) let them escape so
the route returns 500. Making the loop uniformly forgiving would silently turn
those into partial successes, so each stage keeps the behaviour it has. Reducing
the number of fail-open stages is plan phase 8's job, not this one's.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from git_metadata_extractor.pipeline.state import PipelineState

logger = logging.getLogger(__name__)

StageFn = Callable[["PipelineState"], "Awaitable[None] | None"]
StageGate = Callable[["PipelineState"], bool]


def _always(_state: PipelineState) -> bool:
    return True


@dataclass(slots=True)
class Stage:
    """A named, gated step that mutates the pipeline state in place."""

    name: str
    run: StageFn
    applies: StageGate = field(default=_always)
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
    dataclass per step would mean every stage restating the fields it did not
    touch.
    """
    for stage in stages:
        try:
            if not stage.applies(state):
                state.extras[f"{stage.name}_skipped"] = True
                logger.debug("%s: skipped by gate", stage.name)
                continue
        except Exception:
            # A broken gate must not decide the run's fate silently.
            logger.exception("%s: gate raised; treating stage as skipped", stage.name)
            state.warn(f"{stage.name} gate failed; stage skipped")
            continue

        started_at = perf_counter()
        # Published so an adapter can log its own duration *before* returning.
        # The authoritative value below is only written once the stage is done,
        # so an adapter reading `state.timings` mid-stage always saw 0.00s.
        state.stage_started_at = started_at
        try:
            await _invoke(stage, state)
        except Exception as exc:
            elapsed = perf_counter() - started_at
            state.timings[stage.name] = elapsed
            if not stage.fail_open:
                logger.exception("%s stage failed (fatal)", stage.name)
                raise StageError(stage.name, exc) from exc
            logger.exception("%s stage failed", stage.name)
            state.warn(f"{stage.name} stage failed: {exc}")
        else:
            state.timings[stage.name] = perf_counter() - started_at

    return state


def total_seconds(state: PipelineState) -> float:
    return sum(state.timings.values())


def timing_summary(state: PipelineState) -> dict[str, Any]:
    """Slowest stages first — what you want in a log line after a slow run."""
    return {
        name: round(seconds, 3)
        for name, seconds in sorted(
            state.timings.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    }
