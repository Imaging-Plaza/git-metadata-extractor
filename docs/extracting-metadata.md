<!--
Source of truth for this page (verified against code on the feat/deploy-compose branch):
- src/v2/api.py — extract() GET handler ~730, extract_post() POST ~2269, extract_job() GET /jobs/{id} ~2434,
  cancel_extract_job() POST /jobs/{id}/cancel ~2449, crawl_status() GET /crawl/{id} ~2493, _run_extract_job ~531,
  _maybe_mark_extract_job_stale ~2360 (_JOB_HEARTBEAT_INTERVAL_SECONDS=30, _JOB_STALE_THRESHOLD_SECONDS=600),
  _TERMINAL_JOB_STATUSES ~485, health() ~4340 (no auth), JSONLD_CONTEXT_FALLBACK ~237
- src/v2/api_models/contracts.py — V2ExtractRequest ~63, V2ModelOverride ~38, V2ExtractResponse ~113,
  V2ExtractJobStatus ~140, V2ExtractJob ~148, V2ExtractJobAccepted ~164, V2JobStatus ~171
- src/v2/api_models/errors.py — V2ErrorResponse, V2ErrorType
- src/v2/auth.py — verify_token (Bearer against API_TOKEN env var; 503 unset, 401 missing/wrong)
- src/v2/config.py — V2_AGENT_RUNTIME_DEFAULT default=llm; src/v2/dependencies.py:303 V2_PROVIDER_CACHE_ENABLED
- src/v2/ingest/detection/github_url_classifier.py — accepted repo/user/org URL forms; bare form probes GitHub API
- src/v2/schema/json/context/v2.0.jsonld — real @context (schema=http://schema.org/, pulse=https://open-pulse.epfl.ch/ontology#)
- src/v2/schema/json/strict/repository.schema.json — repo @type const = schema:SoftwareSourceCode
- src/v2/pipeline/stages/output_assembly.py — entities_by_type keys; src/v2/ingest/cache.py — TTL get()
- docs/v2-api-reference.md, docs/api-and-cli.md
-->

# Extracting metadata

Turn a GitHub repository, user, or organization URL into structured Open Pulse metadata (JSON-LD or a flat JSON envelope) through the `/v2` extraction API.

This page covers authentication, the synchronous and asynchronous extraction endpoints, every request field, and how to poll and cancel long-running jobs. For the exhaustive endpoint catalogue see [`v2-api-reference.md`](v2-api-reference.md); for a condensed cheat sheet see [`api-and-cli.md`](api-and-cli.md).

!!! note "Base URL"
    Examples use `http://localhost:8000`. The real deployment host is configurable — substitute your own.

## Authenticate

Every extraction endpoint except `GET /v2/health` requires a **bearer token** in the `Authorization` header:

```http
Authorization: Bearer <API_TOKEN>
```

The token is the value of the server-side `API_TOKEN` environment variable. There is no per-user login or token-issuing endpoint — the operator who deploys the service sets `API_TOKEN` and shares it with you. The check uses a constant-time comparison (`hmac.compare_digest`).

| Endpoint | Auth required? |
|---|---|
| `GET /v2/health` | No (open) |
| `GET /v2/extract/{full_path}` | Yes |
| `POST /v2/extract` | Yes |
| `GET /v2/jobs/{job_id}` | Yes |
| `GET /v2/crawl/{job_id}` | Yes |
| `POST /v2/jobs/{job_id}/cancel` | Yes |

Set the token once in your shell so the snippets below work as-is:

```bash
export API_TOKEN="paste-the-value-your-operator-gave-you"
```

Auth failure modes:

| Condition | Status | Notes |
|---|---|---|
| `API_TOKEN` unset on the server | `503` | Fails closed — no dev bypass. |
| `Authorization` header missing | `401` | Response includes `WWW-Authenticate: Bearer`. |
| Wrong token | `401` | Same shape as missing header. |

!!! warning
    A `503` from a protected endpoint means the **server** has no `API_TOKEN` configured, not that your token is wrong. A wrong or missing token is always `401`.

Verify your token works against the open health check first, then a protected route:

```bash
# health is open — no token needed
curl -s "http://localhost:8000/v2/health" | jq

# protected — should return data, not 401
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8000/v2/extract/github.com/octocat/Hello-World?agent_runtime=rule_based" | jq '.detected_type'
```

## Choose sync or async

There are two ways to run an extraction:

| | `GET /v2/extract/{full_path}` | `POST /v2/extract` |
|---|---|---|
| Style | **Synchronous** — blocks until done | **Asynchronous** — returns immediately |
| Returns | `200` + full result | `202` + `job_id` to poll |
| Options passed via | Query string | JSON request body |
| Best for | Quick lookups, `rule_based` runs | LLM/hybrid runs, batch, anything slow |

LLM extractions can take many seconds to minutes. A synchronous `GET` ties up the connection for the whole run and is exposed to client/proxy idle timeouts. **For `agent_runtime=llm` or `hybrid`, prefer the async `POST` + poll flow.** See [Long-running jobs](#long-running-jobs-and-timeouts).

## What URLs are accepted

The `source_url` (POST) or path (GET) is a GitHub URL or bare path. Three shapes are recognised:

| Input kind | Example | `detected_type` |
|---|---|---|
| Repository | `github.com/octocat/Hello-World` | `repository` |
| User | `github.com/cmdoret` | `user` |
| Organization | `github.com/orgs/sdsc-ordes` | `organization` |

!!! note "Bare `github.com/<name>` is disambiguated against the GitHub API"
    A bare account path like `github.com/sdsc-ordes` is ambiguous between a user and an organization. The classifier probes GitHub's `/users/<name>` API to read the actual account `type` and returns `organization` or `user` accordingly. If that probe fails (no GitHub token, network error, or a non-200 response) it falls back to `user`. Use the explicit `github.com/orgs/<name>` form to force organization detection without a probe.

Both `https://github.com/...` and the scheme-less `github.com/...` form work. Anything that does not resolve to one of these shapes is rejected with `422 Unprocessable Entity` and an `error_type` of `unsupported_url` — for the async endpoint this happens synchronously and **no job is created**.

## Synchronous extraction — `GET /v2/extract/{full_path}`

Append the GitHub path directly after `/v2/extract/`. Tune the run with query parameters:

| Query param | Type / allowed values | Default | Meaning |
|---|---|---|---|
| `output_format` | `jsonld` \| `json` | `jsonld` | Response shape (see [Response shape](#response-shape)). |
| `agent_runtime` | `rule_based` \| `llm` \| `hybrid` | server `V2_AGENT_RUNTIME_DEFAULT` (ships as `llm`) | Pipeline runtime. |
| `include_context_summary` | `true` \| `false` | `false` | Attach the compiled LLM context-summary markdown when available. |
| `include_internal_fields` | `true` \| `false` | `false` | Keep `_`-prefixed internal fields (e.g. `_bio`, `_avatar_url`) that are not yet part of the Open Pulse ontology. Does not affect validation, only what you see. |

### Extract a repository (sync, JSON-LD)

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8000/v2/extract/github.com/sdsc-ordes/gimie?output_format=jsonld" | jq
```

Trimmed response (`200 OK`):

```json
{
  "source_url": "https://github.com/sdsc-ordes/gimie",
  "detected_type": "repository",
  "output_format": "jsonld",
  "output": {
    "@context": { "schema": "http://schema.org/", "pulse": "https://open-pulse.epfl.ch/ontology#" },
    "@graph": [
      {
        "@id": "https://github.com/sdsc-ordes/gimie",
        "@type": "schema:SoftwareSourceCode",
        "schema:name": "gimie",
        "schema:codeRepository": "https://github.com/sdsc-ordes/gimie"
      }
    ]
  },
  "warnings": [],
  "stats": { "entities_count": 7, "triples_count": 142, "run_id": "…", "duration_ms": 5123, "stages_completed": ["classify_url", "context_gather"] }
}
```

### Extract a user (sync, deterministic `rule_based`)

`rule_based` is fully deterministic and runs no LLM calls — fast and cheap, ideal for smoke tests.

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8000/v2/extract/github.com/cmdoret?output_format=json&agent_runtime=rule_based" | jq
```

Trimmed response (`200 OK`, `output_format=json`):

```json
{
  "source_url": "https://github.com/cmdoret",
  "detected_type": "user",
  "output_format": "json",
  "output": {
    "root_entity": { "id": "https://github.com/cmdoret", "type": "schema:Person", "schema:name": "cmdoret" },
    "related_entities": [],
    "excluded_entities": [],
    "entities_by_type": { "repositories": [], "persons": [ { "id": "https://github.com/cmdoret" } ], "organizations": [], "articles": [], "memberships": [], "contributions": [] }
  },
  "warnings": [],
  "stats": { "entities_count": 1, "triples_count": 6, "run_id": "…", "duration_ms": 812, "stages_completed": [] }
}
```

## Asynchronous extraction — `POST /v2/extract`

Submit a body, get back a `job_id`, then poll. The handler validates the URL synchronously, creates a `pending` job in the SQLite-backed job store, schedules the pipeline as a background task, and returns `202 Accepted`.

### Request body

`Content-Type: application/json`. Only `source_url` is required; everything else has a default.

| Field | Type / allowed values | Default | Meaning |
|---|---|---|---|
| `source_url` | string (**required**) | — | GitHub repo, user, or org URL/handle. Normalized in the response. |
| `output_format` | `jsonld` \| `json` | `jsonld` | Response shape. |
| `agent_runtime` | `rule_based` \| `llm` \| `hybrid` \| omit | omit → server `V2_AGENT_RUNTIME_DEFAULT` (ships as `llm`) | `rule_based` is deterministic; `llm` adds the agent refiners; `hybrid` runs rule-based then LLM refinement. |
| `include_context_summary` | boolean | `false` | Attach the scout context-summary markdown to the response. |
| `include_internal_fields` | boolean | `false` | Keep `_`-prefixed internal fields. Does not change validation. |
| `model_override` | object \| `null` (see below) | `null` | Per-request LLM model/provider override for `llm`/`hybrid`. **Ignored unless the server sets `V2_ALLOW_REQUEST_MODEL_OVERRIDE`.** |
| `refresh` | boolean | `false` | Bypass caches for this run: skip the pipeline-cache read, re-fetch providers, then overwrite the stale entries. The fresh result is still written back. |

`model_override` (all sub-fields optional; only the ones you set override the server default):

| Sub-field | Meaning |
|---|---|
| `provider` | `openai` \| `openai-compatible` \| `openrouter` \| `ollama` |
| `model` | Model name, e.g. `Qwen/Qwen3-235B`. |
| `base_url` | OpenAI-compatible base URL (e.g. an RCP `/v1`). |
| `api_key_env` | Name of the env var holding the API key (e.g. `RCP_TOKEN`). |

!!! warning "`model_override` is opt-in on the server"
    Because `model_override` can carry a `base_url` / `api_key_env`, it is a mild SSRF / secret surface and stays disabled by default. If `V2_ALLOW_REQUEST_MODEL_OVERRIDE` is not set on the server, the field is silently ignored.

### Submit a repository extraction (async)

```bash
curl -s -X POST "http://localhost:8000/v2/extract" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source_url": "github.com/sdsc-ordes/gimie",
    "output_format": "jsonld",
    "agent_runtime": "llm",
    "include_context_summary": true
  }' | jq
```

Response (`202 Accepted`):

```json
{
  "job_id": "5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31",
  "status": "pending",
  "status_url": "/v2/jobs/5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31",
  "submitted_at": "2026-06-13T10:31:00.000Z"
}
```

`status_url` is the relative path to poll for the full record. Other async error responses:

- `422 Unprocessable Entity` — `source_url` is not a supported GitHub URL (returned synchronously, no job created).
- `503 Service Unavailable` — the provider cache is disabled (`V2_PROVIDER_CACHE_ENABLED=false`), so the async job store has no backing storage.

## Poll a job — `GET /v2/jobs/{job_id}`

Returns the full `V2ExtractJob` record with the latest status.

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8000/v2/jobs/5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31" | jq
```

Response fields:

| Field | When present | Meaning |
|---|---|---|
| `job_id` | always | The id you polled. |
| `status` | always | `pending` \| `running` \| `completed` \| `failed` \| `cancelled`. |
| `request` | always | The original request body, with `source_url` normalized. |
| `submitted_at` | always | ISO-8601 submit time. |
| `started_at` | once running | When the worker picked it up. |
| `completed_at` | terminal states | When it finished/failed/cancelled. |
| `last_heartbeat_at` | once running | Liveness beat (see below). |
| `result` | only `completed` | A full `V2ExtractResponse` (same contract as `GET /v2/extract`). |
| `error` | only `failed` | A `V2ErrorResponse` (`error_type`, `detail`, optional `source_url`, `errors[]`). |

Status responses: `200 OK` for any found record, `404 Not Found` if no job matches the id (also returned once a job ages past the cache TTL), `503 Service Unavailable` if the job store is disabled.

Completed job (trimmed):

```json
{
  "job_id": "5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31",
  "status": "completed",
  "request": { "source_url": "https://github.com/sdsc-ordes/gimie", "output_format": "jsonld", "agent_runtime": "llm" },
  "submitted_at": "2026-06-13T10:31:00.000Z",
  "started_at": "2026-06-13T10:31:01.100Z",
  "completed_at": "2026-06-13T10:31:48.900Z",
  "result": {
    "source_url": "https://github.com/sdsc-ordes/gimie",
    "detected_type": "repository",
    "output_format": "jsonld",
    "output": { "@context": { }, "@graph": [ ] },
    "warnings": [],
    "stats": { "entities_count": 9, "triples_count": 210, "run_id": "…", "duration_ms": 47800, "stages_completed": [] }
  }
}
```

Failed job (trimmed):

```json
{
  "job_id": "…",
  "status": "failed",
  "completed_at": "2026-06-13T10:32:10.000Z",
  "error": {
    "error_type": "pipeline_error",
    "detail": "…what went wrong…",
    "source_url": "https://github.com/sdsc-ordes/gimie"
  }
}
```

### Cheap polling — `GET /v2/crawl/{job_id}`

When you only need the status and not the (potentially large) result graph, poll the lightweight companion. It returns just the lifecycle fields plus a `result_url` pointing at the full record.

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8000/v2/crawl/5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31" | jq
```

```json
{
  "job_id": "5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31",
  "status": "running",
  "source_url": "https://github.com/sdsc-ordes/gimie",
  "submitted_at": "2026-06-13T10:31:00.000Z",
  "started_at": "2026-06-13T10:31:01.100Z",
  "last_heartbeat_at": "2026-06-13T10:31:31.100Z",
  "result_url": "/v2/jobs/5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31"
}
```

### End-to-end: submit, poll, fetch result

```bash
# 1. submit and capture the job id
JOB_ID=$(curl -s -X POST "http://localhost:8000/v2/extract" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_url": "github.com/sdsc-ordes/gimie", "agent_runtime": "llm"}' \
  | jq -r '.job_id')

# 2. poll the cheap status endpoint until terminal
while true; do
  STATUS=$(curl -s -H "Authorization: Bearer $API_TOKEN" \
    "http://localhost:8000/v2/crawl/$JOB_ID" | jq -r '.status')
  echo "status: $STATUS"
  case "$STATUS" in completed|failed|cancelled) break ;; esac
  sleep 5
done

# 3. fetch the full record (result on success, error on failure)
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8000/v2/jobs/$JOB_ID" | jq
```

## Job lifecycle

```
pending ──► running ──► completed
                   ├──► failed
                   └──► cancelled
```

- **pending** — accepted, queued, worker not started yet.
- **running** — the worker is executing the pipeline and writes a heartbeat roughly every 30s.
- **completed** — success; `result` is populated.
- **failed** — the pipeline raised, or the worker died mid-flight (see below).
- **cancelled** — you cancelled it via the cancel endpoint.

`completed`, `failed`, and `cancelled` are terminal — the record never changes after that.

!!! note "Orphaned-job detection"
    If a `running` job stops sending heartbeats for over 10 minutes (the worker process was killed, OOMed, or the service was redeployed), the next poll of `GET /v2/jobs/{job_id}` or `GET /v2/crawl/{job_id}` flips it to `failed` with a `pipeline_error` so you stop polling forever. Resubmit with a fresh `POST /v2/extract`.

## Cancel a job — `POST /v2/jobs/{job_id}/cancel`

Cooperatively cancels a `pending` or `running` job, freeing its worker, and marks the record `cancelled`. Returns the updated full record.

```bash
curl -s -X POST -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8000/v2/jobs/5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31/cancel" | jq '{job_id, status, completed_at}'
```

```json
{
  "job_id": "5d2b8b3d-3e6e-4a82-9b17-1c2f5b6e7a31",
  "status": "cancelled",
  "completed_at": "2026-06-13T10:31:20.000Z"
}
```

Behaviour:

- **Idempotent** — a job already in a terminal state (`completed` / `failed` / `cancelled`) is returned unchanged.
- Cancellation interrupts the pipeline at its next `await` (e.g. an in-flight LLM or HTTP call), so it is not always instantaneous, but the record is marked `cancelled` immediately for an authoritative response.
- `404 Not Found` if no job matches; `503 Service Unavailable` if the job store is disabled.

## Long-running jobs and timeouts

- **Use async for slow runs.** `agent_runtime=llm` and `hybrid` fan out to multiple LLM agents and external lookups; a single repository run can take tens of seconds to several minutes. The synchronous `GET` holds the connection open the whole time and is exposed to client and reverse-proxy idle timeouts. The async `POST` + poll flow sidesteps that entirely.
- **Poll with `GET /v2/crawl/{job_id}`**, not `GET /v2/jobs/{job_id}`, while waiting — it skips the result graph. Switch to `GET /v2/jobs/{job_id}` only once the status is `completed` (or to read the `error` once `failed`). A 5s poll interval is reasonable.
- **Heartbeats and the 10-minute stale window** mean a job whose worker died is reported `failed` rather than hanging at `running` forever — but only after the next poll past the threshold.
- **`refresh: true` is slower** because it bypasses caches and re-fetches every provider. Use it for targeted backfills, not routine reads.
- **Jobs expire.** Records live in the SQLite-backed provider cache and age out after its TTL; once expired, polling returns `404`. Fetch your result before then or resubmit.

## Response shape

`GET /v2/extract` and a completed job's `result` both return the same `V2ExtractResponse`:

- `source_url`
- `detected_type` — `repository` \| `user` \| `organization`
- `output_format` — `jsonld` \| `json`
- `output` — see below
- `warnings` — non-fatal pipeline warnings
- `stats` — run-scoped counts and duration (`entities_count`, `triples_count`, `run_id`, `duration_ms`, `stages_completed`)
- optional `context_summary_markdown` — only when `include_context_summary=true` and a summary exists

When `output_format=json`, `output` is a flat envelope:

```
root_entity: object | null
related_entities: list[object]
excluded_entities: list[object]
entities_by_type: { repositories, persons, organizations, articles, memberships, contributions }
```

When `output_format=jsonld`, `output` is a JSON-LD graph:

```
@context: object
@graph: list[object]
excluded_entities: list[object]   # only when present
```

## Related pages

- [`v2-api-reference.md`](v2-api-reference.md) — full endpoint reference, pipeline stage flow, and environment variables.
- [`api-and-cli.md`](api-and-cli.md) — condensed API + CLI cheat sheet and smoke tests.
- [`migration-v1-to-v2.md`](migration-v1-to-v2.md) — mapping legacy `/v1` calls onto `/v2`.
