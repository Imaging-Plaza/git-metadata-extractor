# V2 API Reference

This document describes the mounted v2 API surface under `/v2`.

## Endpoints

### `GET /v2/health`

Returns service readiness for v2 dependencies.

Example:

```bash
curl -s "http://localhost:1234/v2/health" | jq
```

### `GET /v2/extract/{full_path}`

Runs the v2 extraction pipeline for a GitHub URL path.

Repository-mode traversal contract:

- GitHub traversal is direct-only: source repository, direct owner, and direct contributors.
- GitHub repo-list expansion (`/users/{u}/repos`, `/orgs/{o}/repos`) is disabled.
- ORCID, Infoscience, and ROR enrichment remain enabled for discovered people/organizations.
- `pulse:owns` emitted during repository runs is constrained to the source repository handle when present.

Query parameters:

- `output_format`: `jsonld` (default) or `json`
- `agent_runtime`: `rule_based|llm` (optional; defaults to `V2_AGENT_RUNTIME_DEFAULT=llm`)
  - `agent_runtime=llm` runs LLM agents for repository/user/organization roots and fanout stages.
  - In LLM runtime, two global fail-open stages are always enabled:
    - `llm_dedup` (post-permissive, pre-reconciliation): LLM duplicate-cluster suggestions + deterministic constrained merge/remap.
    - `llm_critic` (post-reconciliation, pre-strict): LLM prune suggestions + deterministic non-root pruning/cascade cleanup.
  - Both stages are warning-only on failure (pipeline continues).
  - Root-stage hard-fail policy in LLM mode (no rule-based fallback):
    - repository input: `repo_agent`
    - user input: `person_agent`
    - organization input: `org_agent`
- `include_intermediates`: `true|false` (default `false`, returns run-scoped intermediate envelopes for this extract run only)
- `include_context_summary`: `true|false` (default `false`, includes the compiled LLM context summary markdown used by downstream agents when available)

Examples:

```bash
curl -s \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=jsonld" \
  | jq
```

```bash
curl -s \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=json&agent_runtime=rule_based" \
  | jq
```

```bash
curl -s \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=json&agent_runtime=llm" \
  | jq
```

### `POST /v2/extract`

Submits an extraction asynchronously. The handler validates the URL synchronously, creates a `pending` job in the `ProviderCache`-backed job store, schedules the pipeline as a background task, and returns `202 Accepted` with a `job_id`. Poll `GET /v2/jobs/{job_id}` to retrieve the result. `GET /v2/extract/{full_path}` remains synchronous and unchanged.

Request body:

- `source_url` (required): GitHub path or URL (for example `github.com/octocat/Hello-World`)
- `output_format`: `jsonld|json` (default `jsonld`)
- `agent_runtime`: `rule_based|llm` (optional)
- `include_context_summary`: `true|false` (default `false`)

Response (`202 Accepted`):

- `job_id`: opaque UUID to poll
- `status`: `pending`
- `status_url`: convenience path (`/v2/jobs/{job_id}`) for the companion GET
- `submitted_at`: ISO-8601 timestamp

Error responses:

- `422 Unprocessable Entity` if `source_url` is unsupported (returned synchronously, no job is created).
- `503 Service Unavailable` if the provider cache is disabled — the async job store has no backing storage.

Example:

```bash
curl -s -X POST "http://localhost:1234/v2/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "source_url": "github.com/octocat/Hello-World",
    "output_format": "json",
    "agent_runtime": "llm",
    "include_context_summary": true
  }' | jq
```

```json
{
  "job_id": "5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31",
  "status": "pending",
  "status_url": "/v2/jobs/5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31",
  "submitted_at": "2026-04-29T10:31:00.000Z"
}
```

### `GET /v2/jobs/{job_id}`

Companion retrieval endpoint for jobs submitted via `POST /v2/extract`. Returns the persisted `V2ExtractJob` record with the latest status.

Response fields:

- `job_id`
- `status`: `pending|running|completed|failed`
- `request`: the original `V2ExtractRequest` payload (with `source_url` normalized)
- `submitted_at`, `started_at`, `completed_at`
- `result`: present only when `status == "completed"`. Same `V2ExtractResponse` contract as `GET /v2/extract/{full_path}` (`source_url`, `detected_type`, `output_format`, `output`, `warnings`, `stats`, optional `context_summary_markdown`).
- `error`: present only when `status == "failed"`. Same `V2ErrorResponse` shape as the error responses on `GET /v2/extract/{full_path}` (`error_type`, `detail`, `source_url`, optional `errors[]`).

Status responses:

- `200 OK` — record found (any status).
- `404 Not Found` — no job exists with that id (also returned for jobs that have aged past the `ProviderCache` TTL).
- `503 Service Unavailable` — provider cache disabled, job store unavailable.

Example:

```bash
curl -s "http://localhost:1234/v2/jobs/5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31" | jq
```

Persistence: jobs share the SQLite-backed `ProviderCache` (`V2_PROVIDER_CACHE_PATH`, TTL `V2_PROVIDER_CACHE_TTL_DAYS`) under the `v2-extract-job` namespace. `V2_PROVIDER_CACHE_ENABLED=false` disables the job store entirely.

### `GET /v2/graph`

Exports graph-store data as JSON-LD.

Query parameters:

- `source_url`: optional source URL used for run-scoped filtering via `runs.stats.entity_ids`
- `entity_type`: repeatable filter (for example `entity_type=person&entity_type=repository`)
- `include_intermediates`: `true|false` (default `true`)
- `intermediate_limit`: per-agent cap applied to returned intermediate envelopes (default from `V2_INTERMEDIATE_HISTORY_LIMIT`)

Example:

```bash
curl -s \
  "http://localhost:1234/v2/graph?source_url=https://github.com/octocat/Hello-World&entity_type=repository" \
  | jq
```

## Runtime Stage Flow

Shared runtime gates for all extract types:

- `permissive_validation`
- `llm_dedup` (LLM runtime only)
- `reconciliation`
- `llm_critic` (LLM runtime only)
- `strict_validation`
- `output_assembly`
- `link_veracity` (always-on, checks discovered links and prunes invalid links/entities)
- `jsonld_build`
- `shacl_gate` (non-fatal warnings)
- `graph_write` (GraphStore upsert path)

`link_veracity` behavior:

- Scans all discovered HTTP(S) links across assembled entities.
- For articles, also validates `schema:identifier` as DOI by normalizing bare DOI strings to `https://doi.org/<doi>`.
- Removes links that are explicitly fetched and marked unreachable (`fetched_successfully=false`).
- Removes entities when their canonical URL/DOI fails validation, or when no valid URL remains on that entity.
- Checker/runtime errors are fail-open (warnings only) and do not auto-prune links/entities.

LLM-stage intermediates (when `include_intermediates=true`):

- `llm_dedup_candidates`
- `llm_dedup_resolution`
- `llm_critic_decisions`
- `llm_critic_applied`

Detected-type execution order before shared gates:

- `repository`: `context_gather -> repo_agent -> person_agents -> org_agents -> article_agents -> membership_agents -> contribution_agents`
- `user`: `context_gather -> person_agent -> repo_agents -> org_agents -> article_agents -> membership_agents -> contribution_agents`
- `organization`: `context_gather -> org_agent -> person_agents -> repo_agents -> article_agents -> membership_agents -> contribution_agents`

## Response Contracts

`/v2/extract` returns:

- `source_url`
- `detected_type`
- `output_format`
- `output`
- `warnings`
- `stats`
- optional `context_summary_markdown` (when `include_context_summary=true` and a compiled summary exists)
- optional `intermediates`

`output_format=json` contract (`output`):

- `root_entity: object | null`
- `related_entities: list[object]`
- `excluded_entities: list[object]`
- `entities_by_type: {repositories, persons, organizations, articles, memberships, contributions}`

`output_format=jsonld` contract (`output`):

- `@context: object`
- `@graph: list[object]`
- optional `excluded_entities` (only when present)

`/v2/graph` returns:

- `graph_jsonld` (must contain `@context` and `@graph`)
- `stats`
- optional `intermediates`

## Graph Write and Source Filtering

- `/v2/extract` writes final included entities (root + related, excluding strict-invalid entities) into GraphStore during `graph_write`.
- `/v2/extract` persists final included IDs into `runs.stats.entity_ids`.
- `/v2/graph?source_url=...` uses those persisted `entity_ids` to build source-scoped subgraphs.

## LLM Agent Architecture

### Single-call model

`LLMRepositoryAgentV2` is a **single pydantic-ai `Agent` call** — it produces all output fields (identifiers, authors, disciplines, license, …) in one prompt/response round-trip. This differs from v1, which split repository extraction across two sequential LLM calls:

1. **Context agent** (`run_llm_analysis` config) — general metadata extraction with Infoscience tools.
2. **Classifier agent** (`run_repository_classifier` config) — dedicated call producing only `repositoryType` + `discipline[]` from the compiled context, with a laser-focused system prompt.

The v1 two-call split produced more reliable discipline classification because the classifier prompt made discipline a required non-nullable output. In v2, `pulse:discipline` is optional and the model tends to omit it when producing all fields at once. The `list_disciplines` tool is intended to guide the model, but a dedicated sub-agent pass (wave 2) would be more robust.

### Token counts

Token counts (`tokens_prompt`, `tokens_completion`) are extracted from `result.usage()` — note the call: pydantic-ai 1.5.0 exposes `usage` as a method on `AgentRunResult`, not a property. The `V2LLMRuntime` resolves this with a `callable` guard before field access. V1 agents accessed `result.usage` without calling it and silently fell back to tiktoken estimates when the counts were 0.

## LLM Agent Tools

LLM agents can call server-side tools during generation. Tools are registered per-agent by passing a `tools=[...]` list to `V2LLMRuntime.run_json_prompt`, which forwards them to the pydantic-ai `Agent`.

Shared tools live in `src/v2/agents/llm/agent_tools/`. Add a new module there to make a tool available to multiple agents.

### Currently registered tools

| Tool name | Module | Used by | Description |
|---|---|---|---|
| `list_disciplines` | `agent_tools/disciplines.py` | `LLMRepositoryAgentV2` | Returns the complete `DisciplineV2` mapping as `[{"wikidata_id": "wd:QXXXXX", "name": "..."}]`. Logs at INFO on each call. |
| `query_dependencies` | `agent_tools/query_dependencies.py` | `LLMRepositoryAgentV2` | Optional. Fetches the parsed SPDX SBOM for the repository via GitHub's dependency-graph REST endpoint and returns a flat `[{name, ecosystem, version, spdxId}]` list. Supports `ecosystem` exact-match and `name_contains` substring filters plus a `limit` cap. Returns `[]` when no SBOM is available (dep graph disabled, private repo without scope, 404). |

### Observability

Tool calls emit an INFO log line from `src.v2.agents.llm.agent_tools.<module>`:

```
INFO src.v2.agents.llm.agent_tools.disciplines: tool call: list_disciplines — returning 46 entries
```

If this line is absent after an LLM repository run with `agent_runtime=llm`, the model did not call the tool.

## Repository Entity Schema Notes

- Repository `identifiers` use `schema:citation` (not `schema:identifier`) for the DOI/citation link. The `idSource` enum values for repositories are `pulse:githubRepositoryHandle`, `schema:citation`, and `uuid`.
- Article entities continue to use `schema:identifier` for their canonical identifier.
- `schema:alternateName` is an internal intermediate field on organization entities: it is consumed by pipeline stages during alias matching and is not present in final `/v2/extract` or `/v2/graph` outputs.

## V2 Environment Variables

| Variable | Default | Notes |
| --- | --- | --- |
| `V2_GRAPH_DB_PATH` | `data/v2_graph.db` | SQLite graph-store path |
| `V2_INTERMEDIATE_HISTORY_LIMIT` | `5` | Max intermediates returned by extract-stage reads and per-agent graph response caps |
| `V2_ENABLE_LOGFIRE` | `true` | Enables v2 Logfire instrumentation |
| `V2_AGENT_RUNTIME_DEFAULT` | `llm` | Default runtime selector for `/v2/extract` when `agent_runtime` query parameter is omitted |
| `LOGFIRE_TOKEN` | unset | Optional Logfire token |
| `GITHUB_TOKEN` | unset | Required for healthy provider preflight |
