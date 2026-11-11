"""Context and mutable state shared by post-agent stages.

The data flows from buckets through reconciled and assembled entities into
payload. Canonical output and the source-attributed substrate are separate
projections of that payload. Warnings, timings and extras accumulate per run.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, JsonValue

from git_metadata_extractor.agents.models import ProviderSet
from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.models import DataModel, InternalModel
from git_metadata_extractor.pipeline.stages.models import (
    AssembledOutput,
    ReconciledEntities,
)
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.detection.models import GitHubURLClassification
from git_metadata_extractor.validation.schema_validation import BatchValidationResult

if TYPE_CHECKING:
    from collections.abc import Iterable


class StageOutcome(DataModel, frozen=True):
    """One invocation; repeated stage names retain separate outcomes."""

    name: str
    status: Literal["completed", "skipped", "failed", "cancelled"]
    phase: Literal["gate", "run"] = "run"
    duration_seconds: float = Field(default=0, ge=0)
    error_type: str | None = None


class PipelineArtifacts(DataModel, validate_assignment=True):
    """Named products used across stage boundaries and response assembly."""

    permissive_entity_count: int = Field(default=0, ge=0)
    llm_dedup_executed: bool = False
    llm_critic_executed: bool = False
    critic_pruned_excluded_entities: list[dict[str, JsonValue]] = Field(default_factory=list)
    strict_batch: BatchValidationResult | None = None
    veracity_records: list[dict[str, JsonValue]] = Field(default_factory=list)
    link_veracity_started_at: float | None = None
    link_veracity_seconds: float = Field(default=0, ge=0)
    extraction_run: dict[str, JsonValue] | None = None
    provenance: dict[str, JsonValue] | None = None
    substrate_graph_sizes: dict[str, int] = Field(default_factory=dict)
    substrate_validation: str | None = None
    substrate_quads_written: int = Field(default=0, ge=0)
    substrate_store_url: str | None = None


class PipelineState(InternalModel, validate_assignment=True):
    """Context, payloads and accumulators for one `/v2/extract` run."""

    # ---- context: set once before the sequence starts, never reassigned ----
    run_id: str
    classification: GitHubURLClassification
    runtime: AgentRuntime
    providers: ProviderSet | None = Field(exclude=True, repr=False)
    output_format: Literal["jsonld", "json"] = "jsonld"
    include_context_summary: bool = False
    include_internal_fields: bool = False
    max_concurrent_agents: int = Field(default=6, ge=1, le=128)
    read_only: bool = False
    jsonld_context: dict[str, Any] = Field(default_factory=dict)
    #: The shared SQLite cache (provider + agent-verdict + pipeline). Stage
    #: context rather than a payload: `llm_critic` passes it through so a
    #: repeated critic call on a known-good graph skips the LLM.
    cache: ProviderCache | None = Field(default=None, exclude=True, repr=False)
    #: When the extraction began, for `prov:startedAtTime`. Set by the route,
    #: which is where the request clock starts.
    started_at: datetime | None = None

    # Agent-stage products the later stages read for prompt context.
    pipeline_outputs: dict[str, Any] = Field(default_factory=dict)
    gathered_context: dict[str, Any] = Field(default_factory=dict)

    # ---- payloads: each stage reads one and may replace it ----
    buckets: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    reconciled: ReconciledEntities | None = None
    assembled: AssembledOutput | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    #: The raw/substrate layer: a JSON-LD document whose top-level `@graph`
    #: holds one named graph per `pulse:ExtractionOutput`. A fifth payload
    #: rather than a replacement for `payload`, because it is a *sibling*
    #: projection of the same intermediate, not a further step over it — the
    #: canonical layer keeps what a consumer should query, the substrate keeps
    #: what each source actually said. `None` when the substrate stage is off.
    substrate: dict[str, Any] | None = None

    # ---- accumulators ----
    warnings: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)
    #: `perf_counter()` reading from when the running stage began, published by
    #: `runner.run_pipeline`. Adapters log their own per-stage line while still
    #: inside the stage, when `timings` has no entry yet — reading `timings`
    #: there reported `0.00s` for every stage. Deliberately a field rather than
    #: an `extras` key: `extras` carries values bound for the response, and a
    #: private runner marker does not belong in it.
    stage_started_at: float | None = None
    artifacts: PipelineArtifacts = Field(default_factory=PipelineArtifacts)
    stage_outcomes: list[StageOutcome] = Field(default_factory=list)
    # Extension-owned JSON metadata only; production stages use artifacts.
    extras: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def detected_type(self) -> str:
        return str(self.classification.detected_type.value)

    @property
    def source_url(self) -> str:
        return str(self.classification.normalized_url)

    def warn(self, message: str) -> None:
        """Record a warning, ignoring exact duplicates.

        Stages emit the same
        message once per affected entity, and the response carried duplicates
        before that helper existed.
        """
        if message and message not in self.warnings:
            self.warnings.append(message)

    def warn_all(self, messages: Iterable[str] | None) -> None:
        for message in messages or ():
            self.warn(str(message))
