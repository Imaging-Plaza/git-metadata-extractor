from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.v2.api_models.errors import V2ErrorResponse
from src.v2.observation.github_rate_limit import GitHubRateLimitSummary


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


class V2ExtractResponse(BaseModel):
    source_url: str
    detected_type: Literal["repository", "user", "organization"]
    output_format: Literal["jsonld", "json"]
    output: V2JSONLDOutput | V2JSONOutputEnvelope
    context_summary_markdown: str | None = None
    warnings: list[str] = Field(default_factory=list)
    stats: V2Stats

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


class IndexIngestJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ZenodoIngestRequest(BaseModel):
    """Body for `POST /v2/indices/zenodo/ingest`."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(
        min_length=1,
        description=(
            "One or more Zenodo record identifiers. Bare numeric ids, "
            "DOIs (`10.5281/zenodo.…`), or full Zenodo URLs are accepted."
        ),
    )
    refresh: bool = Field(
        default=False,
        description="If true, re-fetch records already present in the local store.",
    )


class HFIngestItem(BaseModel):
    """One repository to ingest into the HuggingFace index."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["model", "dataset", "space"]
    repo_id: str = Field(
        min_length=1,
        description="HuggingFace repo handle. Format: `<author>/<name>`.",
    )


class HuggingFaceIngestRequest(BaseModel):
    """Body for `POST /v2/indices/huggingface/ingest`."""

    model_config = ConfigDict(extra="forbid")

    items: list[HFIngestItem] = Field(
        min_length=1,
        description="One or more {type, repo_id} pairs to ingest.",
    )


class GitHubIngestRequest(BaseModel):
    """Body for `POST /v2/indices/github/ingest`."""

    model_config = ConfigDict(extra="forbid")

    repos: list[str] = Field(
        min_length=1,
        description="One or more GitHub repo handles in the form `owner/name`.",
    )


class GitHubUsersIngestRequest(BaseModel):
    """Body for `POST /v2/indices/github_users/ingest`."""

    model_config = ConfigDict(extra="forbid")

    logins: list[str] = Field(
        min_length=1,
        description="One or more GitHub user logins (bare handles, not URLs).",
    )


class GitHubOrgsIngestRequest(BaseModel):
    """Body for `POST /v2/indices/github_organizations/ingest`."""

    model_config = ConfigDict(extra="forbid")

    orgs: list[str] = Field(
        min_length=1,
        description="One or more GitHub organization handles (bare, not URLs).",
    )


class OpenAlexIngestRequest(BaseModel):
    """Body for `POST /v2/indices/openalex/ingest`.

    Accepts OpenAlex work identifiers in any of the canonical forms: a short
    ``W…`` id, an ``https://openalex.org/W…`` URL, or a DOI (`10.…`).
    """

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(
        min_length=1,
        description="One or more OpenAlex work IDs (`W…`), URLs, or DOIs.",
    )


class OrcidIngestRequest(BaseModel):
    """Body for `POST /v2/indices/orcid/ingest`."""

    model_config = ConfigDict(extra="forbid")

    orcid_ids: list[str] = Field(
        min_length=1,
        description="One or more ORCID identifiers (`XXXX-XXXX-XXXX-XXXX`).",
    )


class RenkulabIngestRequest(BaseModel):
    """Body for `POST /v2/indices/renkulab/ingest`.

    Currently scoped to v2 project records; additional entity types can be
    added later without breaking the contract.
    """

    model_config = ConfigDict(extra="forbid")

    project_ids: list[str] = Field(
        min_length=1,
        description="One or more Renku v2 project ids (slug or UUID).",
    )


class SwissubaseIngestRequest(BaseModel):
    """Body for `POST /v2/indices/swissubase/ingest`."""

    model_config = ConfigDict(extra="forbid")

    study_ids: list[str] = Field(
        min_length=1,
        description="One or more SWISSUbase numeric study ids.",
    )


class EthzResearchCollectionIngestRequest(BaseModel):
    """Body for `POST /v2/indices/ethz_research_collection/ingest`."""

    model_config = ConfigDict(extra="forbid")

    uuids: list[str] = Field(
        min_length=1,
        description=(
            "One or more ETH Research Collection item UUIDs "
            "(DSpace `/core/items/{uuid}`)."
        ),
    )


class OamonitorIngestItem(BaseModel):
    """One Open Access Monitor (OAM-CH) document to ingest."""

    model_config = ConfigDict(extra="forbid")

    entity: Literal[
        "journals", "publications", "publishers", "organisations",
    ] = Field(
        description="OAM-CH collection the id belongs to.",
    )
    id: str = Field(
        min_length=1,
        description=(
            "Upstream `_id` of the document (string ids for journals/publishers, "
            "OpenAlex URLs for publications, ROR URLs for organisations)."
        ),
    )


class OamonitorIngestRequest(BaseModel):
    """Body for `POST /v2/indices/oamonitor/ingest`."""

    model_config = ConfigDict(extra="forbid")

    items: list[OamonitorIngestItem] = Field(
        min_length=1,
        description="One or more {entity, id} pairs to ingest from OAM-CH.",
    )


class IndexSearchRequest(BaseModel):
    """Body for `POST /v2/indices/<name>/search`.

    Uniform across indices. Indices with a single entity type ignore
    ``target``; multi-entity indices use it to select the collection.
    ETHZ Research Collection accepts the ChromaDB-style ``filter_payload``
    as its ``where`` clause and falls back to ``mode="hybrid"``.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        description="Free-text query to match against the index.",
    )
    top_k: int = Field(
        default=10, ge=1, le=200,
        description="Maximum number of results to return.",
    )
    candidate_k: int | None = Field(
        default=None, ge=1, le=1000,
        description="Vector-search candidate count before reranking. Indices that do not rerank ignore this.",
    )
    filter_payload: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata filter dict. Shape is index-specific (Qdrant for most, ChromaDB-style `where` for ETHZ Research Collection).",
    )
    target: str | None = Field(
        default=None,
        description="Optional entity type / collection target for multi-entity indices (e.g. huggingface: model|dataset|space|org; openalex: works|authors|institutions|sources|topics|concepts; ethz_research_collection: chunks|articles|persons|organizations).",
    )


class IndexSearchHit(BaseModel):
    """One result row returned by an index search."""

    model_config = ConfigDict(extra="allow")

    id: str
    vector_score: float | None = None
    rerank_score: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    entity: dict[str, Any] | None = None


class IndexSearchResponse(BaseModel):
    """Wrapper envelope for index search results."""

    index_name: "IndexName"
    target: str | None = None
    query: str
    hits: list[IndexSearchHit] = Field(default_factory=list)
    extra: dict[str, Any] | None = Field(
        default=None,
        description="Index-specific extras (e.g. ETHZ Research Collection related persons/orgs, HuggingFace facets). Optional.",
    )


IndexName = Literal[
    "zenodo",
    "huggingface",
    "github",
    "github_users",
    "github_organizations",
    "openalex",
    "orcid",
    "renkulab",
    "swissubase",
    "ethz_research_collection",
    "oamonitor",
    # CLI-managed catalogs — search routes added in the stats/search
    # coverage extension PR. No v2 ingest route (ingest happens via
    # `python -m src.index.<name> ingest`).
    "ror",
    "infoscience",
    "snsf",
    "epfl_graph",
    "communities",
]


class IndexIngestJob(BaseModel):
    """Persistent record for an async index-ingest job."""

    job_id: str
    index_name: IndexName
    status: IndexIngestJobStatus
    request: dict[str, Any]
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: dict[str, Any] | None = None
    error: str | None = None


class IndexIngestJobAccepted(BaseModel):
    """Response body for the POST that enqueues an ingest job."""

    job_id: str
    index_name: IndexName
    status: IndexIngestJobStatus
    status_url: str
    submitted_at: datetime
