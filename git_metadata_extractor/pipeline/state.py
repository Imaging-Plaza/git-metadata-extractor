"""The value that flows through the post-agent pipeline.

Before this existed, `api/extract.py::extract` held ~30 local variables and
threaded them by hand through 33 stage calls, which is why the sequence could
only live inside one 1,110-line function. Collecting them here is what lets
`pipeline/runner.py` give every stage the same signature.

Four payloads, not one, because that is what the stages actually operate on
today:

    buckets   -> reconciled -> assembled -> payload
    (dict)       (Reconciled)  (Assembled)  (JSON-LD dict)

That is deliberately *not* an RDF graph yet. Swapping these four for one graph
rewrites every stage's internals, so it belongs with the substrate writer (plan
phase 5) rather than in the mechanical move that introduced this file — a phase
whose whole value is that the corpus diff stays empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from git_metadata_extractor.agents.runtime import AgentRuntime
    from git_metadata_extractor.pipeline.stages.models import (
        AssembledOutput,
        ReconciledEntities,
    )


@dataclass(slots=True)
class PipelineState:
    """Context, payloads and accumulators for one `/v2/extract` run."""

    # ---- context: set once before the sequence starts, never reassigned ----
    run_id: str
    classification: Any
    runtime: AgentRuntime
    providers: Any
    output_format: str = "jsonld"
    include_context_summary: bool = False
    include_internal_fields: bool = False
    max_concurrent_agents: int = 6
    jsonld_context: dict[str, Any] = field(default_factory=dict)
    #: The shared SQLite cache (provider + agent-verdict + pipeline). Stage
    #: context rather than a payload: `llm_critic` passes it through so a
    #: repeated critic call on a known-good graph skips the LLM.
    cache: Any = None

    # Agent-stage products the later stages read for prompt context.
    pipeline_outputs: dict[str, Any] = field(default_factory=dict)
    gathered_context: dict[str, Any] = field(default_factory=dict)

    # ---- payloads: each stage reads one and may replace it ----
    buckets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    reconciled: ReconciledEntities | None = None
    assembled: AssembledOutput | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    # ---- accumulators ----
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    #: `perf_counter()` reading from when the running stage began, published by
    #: `runner.run_pipeline`. Adapters log their own per-stage line while still
    #: inside the stage, when `timings` has no entry yet — reading `timings`
    #: there reported `0.00s` for every stage. Deliberately a field rather than
    #: an `extras` key: `extras` carries values bound for the response, and a
    #: private runner marker does not belong in it.
    stage_started_at: float | None = None
    # Per-stage bookkeeping the response and stats need (`llm_dedup_executed`,
    # `critic_pruned_excluded_entities`, `link_veracity_seconds`, ...). Kept as
    # a bag rather than 12 typed fields so adding a stage does not touch this
    # module; `compute_stats` is the only consumer that cares about the keys.
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def detected_type(self) -> str:
        return str(self.classification.detected_type.value)

    @property
    def source_url(self) -> str:
        return str(self.classification.normalized_url)

    def warn(self, message: str) -> None:
        """Record a warning, ignoring exact duplicates.

        Mirrors `api/extract.py::_append_unique_warning`. Stages emit the same
        message once per affected entity, and the response carried duplicates
        before that helper existed.
        """
        if message and message not in self.warnings:
            self.warnings.append(message)

    def warn_all(self, messages: Any) -> None:
        for message in messages or ():
            self.warn(str(message))
