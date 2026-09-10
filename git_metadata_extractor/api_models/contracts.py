from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from git_metadata_extractor.api_models.errors import V2ErrorResponse
from git_metadata_extractor.observation.github_rate_limit import GitHubRateLimitSummary

# Upper bound on per-request ingest batch size — caps resource use on the
# (token-gated) /v2/indices/*/ingest endpoints (audit: ingest-list-no-maxlen).
_MAX_INGEST_BATCH = 1000
# Upper bound on a free-text search query (audit: search-query-unbounded-string).
_MAX_QUERY_CHARS = 4000


class V2Stats(BaseModel):
    entities_count: int
    triples_count: int
    run_id: str
    duration_ms: int
    stages_completed: list[str] = Field(default_factory=list)


class V2JSONLDOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    context: dict[str, Any] = Field(alias="@context")
    graph: list[dict[str, Any]] = Field(alias="@graph")
    excluded_entities: list[dict[str, Any]] | None = None


class V2JSONOutputEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_entity: dict[str, Any] | None
    related_entities: list[dict[str, Any]]
    excluded_entities: list[dict[str, Any]]
    entities_by_type: dict[str, list[dict[str, Any]]]


class V2ModelOverride(BaseModel):
    """Per-request LLM model/provider override.

    Only honored when the server sets ``V2_ALLOW_REQUEST_MODEL_OVERRIDE`` (it
    can carry a ``base_url``/``api_key_env``, a mild SSRF / secret surface that
    stays opt-in). Lets a single ``/v2/extract`` target a different chat model
    or endpoint — e.g. an RCP OpenAI-compatible model — without editing the
    global deploy config. All fields optional; only the provided ones override.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(
        default=None,
        description="Provider kind: 'openai', 'openai-compatible', 'openrouter', 'ollama'.",
    )
    model: str | None = Field(default=None, description="Model name, e.g. 'Qwen/Qwen3-235B'.")
    base_url: str | None = Field(
        default=None, description="OpenAI-compatible base URL (e.g. RCP '/v1').",
    )
    api_key_env: str | None = Field(
        default=None, description="Env var holding the API key (e.g. 'RCP_TOKEN').",
    )


class V2ExtractRequest(BaseModel):
    source_url: str = Field(
        description="GitHub repository, user, or organization URL or handle.",
        examples=["https://github.com/sdsc-ordes/gimie"],
    )
    output_format: Literal["jsonld", "json"] = Field(
        default="jsonld",
        description="Response shape: `jsonld` (JSON-LD graph) or `json` (flat envelope).",
    )
    agent_runtime: Literal["rule_based", "llm", "hybrid"] | None = Field(
        default=None,
        description=(
            "Pipeline runtime. `rule_based` is deterministic; `llm` adds the "
            "agent refiners; `hybrid` runs rule-based then LLM refinement. "
            "Falls back to the server's V2_AGENT_RUNTIME_DEFAULT when omitted."
        ),
    )
    include_context_summary: bool = Field(
        default=False,
        description="When true, attaches the scout context summary to the response.",
    )
    include_internal_fields: bool = Field(
        default=False,
        description=(
            "When true, the response keeps `_`-prefixed internal fields "
            "(e.g. `_bio`, `_avatar_url`, `_orcid_keywords`, `_company`) that "
            "aren't part of the Open Pulse ontology yet. Strict SHACL "
            "validation still runs identically — this flag only affects what "
            "the consumer sees. Default false for ontology compliance."
        ),
    )
    model_override: V2ModelOverride | None = Field(
        default=None,
        description=(
            "Per-request LLM model/provider override for `llm`/`hybrid` runtimes. "
            "Only applied when the server enables V2_ALLOW_REQUEST_MODEL_OVERRIDE; "
            "ignored otherwise. Target a different chat model/endpoint for a single run."
        ),
    )
    refresh: bool = Field(
        default=False,
        description=(
            "Bypass caches for this extraction (targeted backfill): skip the "
            "pipeline-cache read and re-fetch providers, then overwrite the stale "
            "entries — so a single re-extract picks up current pipeline logic "
            "without clearing the whole cache. The result is still written back."
        ),
    )


class V2ExtractResponse(BaseModel):
    source_url: str
    detected_type: Literal["repository", "user", "organization"]
    output_format: Literal["jsonld", "json"]
    output: V2JSONLDOutput | V2JSONOutputEnvelope
    context_summary_markdown: str | None = None
    warnings: list[str] = Field(default_factory=list)
    stats: V2Stats
    #: A two-node JSON-LD graph describing this run as a `pulse:ExtractionRun`
    #: plus the `prov:SoftwareAgent` that produced it. Sibling of `output`, not
    #: part of it: the four-layer model keeps runs in the provenance layer
    #: while `output` carries what was extracted. Optional, so older consumers
    #: and cached responses predating the field stay valid.
    extraction_run: dict[str, Any] | None = None
    #: The raw/substrate layer for this run: a JSON-LD document whose
    #: top-level `@graph` holds one **named graph** per
    #: `pulse:ExtractionOutput` — one platform's slice of one run — plus a
    #: meta graph carrying the outputs and the `ExtractionRun`. Present only
    #: when `V2_SUBSTRATE_ENABLED` is on.
    #:
    #: Sibling of `output` for the same reason `extraction_run` is: `output`
    #: is the canonical graph, what a consumer should query, and the substrate
    #: is what each source actually said before anything was chosen between
    #: them. It is also where the substrate *writer* sends its payload, so
    #: this field is the same bytes the store received — which is what makes
    #: a store write checkable from the response alone.
    substrate: dict[str, Any] | None = None

    @model_validator(mode="after")
    def output_matches_format(self) -> V2ExtractResponse:
        if self.output_format == "jsonld" and not isinstance(self.output, V2JSONLDOutput):
            message = "output must match jsonld contract when output_format=jsonld"
            raise ValueError(message)
        if self.output_format == "json" and not isinstance(self.output, V2JSONOutputEnvelope):
            message = "output must match json envelope contract when output_format=json"
            raise ValueError(message)
        return self


class V2HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    components: dict[str, Literal["healthy", "degraded", "unhealthy"]]
    version: str
    github_rate_limit: GitHubRateLimitSummary | None = None


class V2ExtractJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class V2ExtractJob(BaseModel):
    job_id: str
    status: V2ExtractJobStatus
    request: V2ExtractRequest
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Heartbeat written periodically by the running worker. The GET
    # endpoint flips status to FAILED when this is stale for too long,
    # so jobs whose worker process died mid-flight are reported as
    # failed rather than perpetually "running".
    last_heartbeat_at: datetime | None = None
    result: V2ExtractResponse | None = None
    error: V2ErrorResponse | None = None


class V2ExtractJobAccepted(BaseModel):
    job_id: str
    status: V2ExtractJobStatus
    status_url: str
    submitted_at: datetime


class V2JobStatus(BaseModel):
    """Compact status view of an extract job — the lifecycle fields without
    the (potentially large) `result` graph.

    Served by `GET /v2/crawl/{job_id}` for cheap polling and v1-style
    parity; the full record + extracted graph stays at `result_url`
    (`GET /v2/jobs/{job_id}`).
    """

    job_id: str
    status: V2ExtractJobStatus
    source_url: str | None = None
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    error: V2ErrorResponse | None = None
    result_url: str

# --------------------------------------------------------------------------
# the graph query API (phase 7)
# --------------------------------------------------------------------------


class V2GraphStatusResponse(BaseModel):
    """What the accumulated store holds. The operator's first question."""

    store_url: str
    named_graphs: int
    triples: int
    #: Split out because they answer different questions: canonical is what a
    #: consumer queries, provenance is why. A provenance count of 0 with a
    #: healthy canonical count is the normal state today — see the note on the
    #: derived-only grain in `unify/provenance.py`.
    canonical_triples: int
    provenance_triples: int
    extraction_runs: int
    canonical_entities_by_type: dict[str, int] = Field(default_factory=dict)


class V2GraphEntityResponse(BaseModel):
    """One canonical entity, in the same JSON-LD shape `/v2/extract` returns.

    Deliberately the same shape: a caller reading an entity out of the store
    and a caller reading one out of an extraction should not need two parsers.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    context: dict[str, Any] = Field(alias="@context")
    graph: list[dict[str, Any]] = Field(alias="@graph")


class V2ProvenanceRecord(BaseModel):
    """One chosen value, and how it was chosen.

    Flat rather than JSON-LD because it describes an RDF-star quoted triple,
    and the pinned rdflib cannot serialise one — so this is built from SPARQL
    bindings. That is a permanent property of `graph:prov`, not a stopgap.
    """

    property: str
    value: str | None = None
    #: The `pulse:ExtractionOutput` the winning value was read from — one
    #: platform's slice of one run, which is the granularity that answers
    #: "which source said this".
    derived_from: str | None = None
    #: The run that produced that output, resolved through
    #: `prov:wasGeneratedBy` so a caller needs one request rather than two.
    run: str | None = None
    observation_kind: str | None = None
    observation_count: int | None = None
    first_observed_on: str | None = None
    last_confirmed_on: str | None = None
    observed_on: str | None = None


class V2GraphProvenanceResponse(BaseModel):
    """Why each chosen value for one subject won.

    An empty `records` list means no value on this subject was *contested* —
    not that provenance is missing. A single-source value is already attributed
    by the named graph it sits in, so nothing is recorded for it.
    """

    subject: str
    records: list[V2ProvenanceRecord] = Field(default_factory=list)
    #: `owl:sameAs` subproperty links, which live in `graph:prov` as plain
    #: triples rather than quoted-triple annotations — the ontology is explicit
    #: about that, and the closed canonical shapes reject them anyway.
    same_as: list[str] = Field(default_factory=list)
