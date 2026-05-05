# V2 API Reference

This document describes the mounted v2 API surface under `/v2`.

## Authentication

`/v2/extract` (both `GET` and `POST`) and `/v2/jobs/{job_id}` require a
bearer token in the `Authorization` header. `/v2/health` is the only v2
endpoint that stays open.

```http
Authorization: Bearer <API_TOKEN>
```

The expected token comes from the server-side `API_TOKEN` env var
(generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
The check uses `hmac.compare_digest` for constant-time comparison.

| Condition | Status | Notes |
|---|---|---|
| `API_TOKEN` unset on the server | `503` | Fails closed; no dev bypass. |
| Header missing | `401` | Includes `WWW-Authenticate: Bearer`. |
| Wrong token | `401` | Same response shape as missing header. |
| Valid token | route's normal response | |

`/v1/*` routes share the same `API_TOKEN` and behave the same way.
Implementation lives in `src/v2/auth.py` (`verify_token` dependency).

## Endpoints

### `GET /v2/health`

Returns service readiness for v2 dependencies. Open (no auth required).

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
    - `llm_critic` (post-reconciliation, pre-strict, gated by `V2_APPLY_CRITIC_PRUNING`): LLM prune suggestions + deterministic non-root pruning/cascade cleanup.
  - Both stages are warning-only on failure (pipeline continues).
  - Root-stage hard-fail policy in LLM mode (no rule-based fallback):
    - repository input: `repo_agent`
    - user input: `person_agent`
    - organization input: `org_agent`
- `include_context_summary`: `true|false` (default `false`, includes the compiled LLM context summary markdown used by downstream agents when available)

Examples:

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=jsonld" \
  | jq
```

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=json&agent_runtime=rule_based" \
  | jq
```

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" \
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
  -H "Authorization: Bearer $API_TOKEN" \
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
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:1234/v2/jobs/5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31" | jq
```

Persistence: jobs share the SQLite-backed `ProviderCache` (`V2_PROVIDER_CACHE_PATH`, TTL `V2_PROVIDER_CACHE_TTL_DAYS`) under the `v2-extract-job` namespace. `V2_PROVIDER_CACHE_ENABLED=false` disables the job store entirely.

## Runtime Stage Flow

`/v2/extract` runs the same pipeline regardless of `agent_runtime`. The
runtime only controls which agent implementations execute (LLM agents vs.
deterministic rule-based agents). Other stages run unconditionally.

```
1.  classify_url               classify the input as repository / user / org
2.  gather_context             fetch GitHub metadata + GIMIE JSON-LD
3.  context_summary  [LLM]     compile a markdown summary; raw blobs are stripped
                               from per-agent prompts in LLM mode
4.  repo_agent                 produce the root repository entity
5.  person_agents (fan-out)    one agent per discovered contributor / user
6.  org_agents    (fan-out)    one agent per discovered organisation
7.  article_agents (fan-out)   discover scholarly articles tied to the repo
8.  membership_agents (fan-out) one agent per (person, org) pair
9.  contribution_agents (fan-out) one agent per (person, repo) pair
10. llm_dedup       [LLM]      cross-bucket entity dedup + ID remap (fail-open)
11. reconcile_entities         deterministic ID canonicalisation + linkage
12. llm_critic      [LLM, gated] drop-suggestion stage (off by default)
13. guarantee_repo_author      stamp github-owner as schema:author when empty
14. strict_validation          per-entity strict JSON Schema check
15. assemble_output            split graph into root + related + excluded
16. link_veracity   [LLM, gated] verify every URL via Selenium fetch + LLM
17. validate_articles          drop placeholder / sentinel-DOI articles
18. validate_author_classes    drop `schema:author` refs whose target is
                               not a `schema:Person`
19. validate_ownership         strip mismatched pulse:owns
20. infer_owners               stamp pulse:owns / pulse:ownedBy from handles;
                               coerces residual bare-login strings on
                               `pulse:ownedBy` to `{"@id": "https://github.com/{handle}"}`
21. infer_github_handle_parents  fuzzy-search ROR for parent of every github
                                 org; add ROR org entities, stamp unitOf
22. org_relationships [LLM]    whole-graph LLM call to refine unitOf edges
23. infer_org_units            deterministic name-token fallback for unitOf
24. build_jsonld_output        produce the final JSON-LD graph; strips
                               redundant `pulse:ror` from any
                               `org:Organization` whose `@id` is already
                               the ROR (closed-shape fix)
```

**Gates:**

- Stages tagged `[LLM]` only run in `agent_runtime=llm`.
- `link_veracity` is `[LLM]`-only: in `agent_runtime=rule_based` it is **always skipped** (rule-based mode is guaranteed LLM-free).
- `llm_critic` is **off by default**. Set `V2_APPLY_CRITIC_PRUNING=true` to enable (LLM mode only).
- `link_veracity` is **on by default in LLM mode**. Set `V2_LINK_VERACITY_ENABLED=false` to skip even in LLM mode (recommended for batch runs).

`link_veracity` behavior:

- Scans all discovered HTTP(S) links across assembled entities.
- For articles, validates `schema:identifier` as DOI (bare DOI strings are normalized to `https://doi.org/<doi>`).
- Removes links explicitly fetched and marked unreachable.
- Removes entities when their canonical URL/DOI fails validation, or when no valid URL remains.
- Checker/runtime errors are fail-open (warnings only) and do not auto-prune.

## Response Contracts

`/v2/extract` returns:

- `source_url`
- `detected_type`
- `output_format`
- `output`
- `warnings`
- `stats`
- optional `context_summary_markdown` (when `include_context_summary=true` and a compiled summary exists)

`output_format=json` contract (`output`):

- `root_entity: object | null`
- `related_entities: list[object]`
- `excluded_entities: list[object]`
- `entities_by_type: {repositories, persons, organizations, articles, memberships, contributions}`

`output_format=jsonld` contract (`output`):

- `@context: object`
- `@graph: list[object]`
- optional `excluded_entities` (only when present)

## LLM Agent Architecture

Each entity bucket has a dedicated agent under `src/v2/agents/llm/<kind>/agent.py`
(repository, person, organization, article, membership, contribution).
Each is a **single pydantic-ai `Agent` call** that produces all output
fields in one prompt/response round-trip. Hallucination guards baked into
agents:

- `force_server_uuid` overwrites whatever UUID the LLM emitted with a
  server-generated one in `identifiers.uuid` only.
- Repository agent post-LLM: stars/forks from GitHub REST (deterministic);
  discipline fallback `wd:Q428691` (computer engineering) when the LLM
  emits empty/null.
- Contribution agent post-LLM: stamps `schema:author` and
  `pulse:contributionTo` from the orchestrator's authoritative pair.
- Article agent post-LLM: drops the entity if `schema:identifier` is a
  placeholder DOI (`10.0000/...`) or sentinel string (`UNKNOWN`, `N/A`,
  `TBD`, …) and no `pulse:infoscienceArticleIdentifier` is present.

Token counts (`tokens_prompt`, `tokens_completion`) are extracted from
`result.usage()` (pydantic-ai 1.5+ exposes `usage` as a method on
`AgentRunResult`, not a property — the `V2LLMRuntime` resolves this with
a `callable` guard).

## LLM Agent Tools

LLM agents can call server-side tools during generation. Tools are
registered per-agent by passing a `tools=[...]` list to
`V2LLMRuntime.run_json_prompt`, which forwards them to the pydantic-ai
`Agent`. Shared tools live in `src/v2/agents/llm/agent_tools/` — add a new
module there to make a tool available to multiple agents.

Two main families:

- **Per-index RAG tools** (`*_rag.py`) — Qdrant-backed semantic search +
  on-demand fetch over Infoscience, ETH Research Collection, HuggingFace,
  OpenAlex, ORCID, ROR, Zenodo. Plus the **federated** tools
  (`search_federated_rag`, `lookup_entity_federated`) that fan out across
  all nine indices in parallel. Documented in
  [`v2-rag-tools.md`](v2-rag-tools.md).
- **Direct provider tools** — disciplines list (`list_disciplines`),
  GitHub SPDX SBOM (`query_dependencies`), GitHub org / ROR org / ORCID
  person identity lookups, Infoscience search and orgunit, repository
  corpus grep, Selenium fetch (`fetch_link_content_via_selenium`), email
  hashing, UUID generation, DuckDuckGo search.

The full registered set lives in
`src/v2/agents/llm/agent_tools/__init__.py`.

### Observability

Tool calls emit an INFO log line from `src.v2.agents.llm.agent_tools.<module>`:

```
INFO src.v2.agents.llm.agent_tools.disciplines: tool call: list_disciplines — returning 46 entries
```

If this line is absent after an LLM repository run with
`agent_runtime=llm`, the model did not call the tool.

## Repository Entity Schema Notes

- Repository `identifiers` use `schema:citation` (not `schema:identifier`) for the DOI/citation link. The `idSource` enum values for repositories are `pulse:githubRepositoryHandle`, `schema:citation`, and `uuid`.
- Article entities continue to use `schema:identifier` for their canonical identifier.
- `schema:alternateName` is an internal intermediate field on organization entities: it is consumed by pipeline stages during alias matching and is not present in final `/v2/extract` outputs.
- Internal pipeline metadata fields whose names start with `_` (e.g. `_person_ref` on Memberships) are stripped before strict validation, JSON-LD output, RDF serialisation, and any external artefact.

## V2 Environment Variables

The most-touched knobs (full list in `.env.example` and `CLAUDE.md`):

| Variable | Default | Notes |
| --- | --- | --- |
| `API_TOKEN` | unset | Bearer token guarding `/v1/*` and protected `/v2/*` routes. Missing → 503 (no dev bypass). See [Authentication](#authentication). |
| `V2_AGENT_RUNTIME_DEFAULT` | `llm` | Default runtime when `/v2/extract` omits `agent_runtime` |
| `V2_USE_MOCK_PROVIDERS` | `true` | Swap in mock GitHub/ORCID/Infoscience/ROR providers |
| `V2_LINK_VERACITY_ENABLED` | `true` | Skip the link-veracity stage in LLM mode (rule-based skips unconditionally) |
| `V2_APPLY_CRITIC_PRUNING` | `false` | Enable critic drop suggestions (LLM mode only) |
| `V2_MAX_CONCURRENT_AGENTS` | `6` | Per-stage fan-out concurrency |
| `V2_PROVIDER_CACHE_PATH` | `.cache/v2/providers.db` | Shared provider + verdict + pipeline + job-store SQLite path |
| `V2_PROVIDER_CACHE_TTL_DAYS` | `30` | TTL for cached entries |
| `V2_PROVIDER_CACHE_ENABLED` | `true` | When `false`, every external lookup is fresh and the async-job store is disabled |
| `V2_PIPELINE_CACHE_ENABLED` | `true` | When `false`, every `/extract` re-runs the full pipeline |
| `V2_<INDEX>_RAG_ENABLED` | `true` | Toggle each RAG tool family (`INFOSCIENCE`, `ETHZ_RESEARCH_COLLECTION`, `HUGGINGFACE`, `OPENALEX`, `ZENODO`, `ORCID`, `ROR`) |
| `INDEX_QDRANT_URL` | YAML-driven | Qdrant endpoint for every RAG index. Use `http://gme-qdrant:6333` inside the devcontainer. |
| `V2_QUERY_LOG_DIR` | `logs/v2_queries` | Per-request external-query log destination |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `GITHUB_TOKEN` | unset | Required for healthy provider preflight |
| `RCP_TOKEN` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` | unset | At least one required in LLM mode |
| `SELENIUM_REMOTE_URL` | unset | Enables link-veracity + the `fetch_link_content_via_selenium` tool |
