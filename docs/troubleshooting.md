<!--
Source grounding (verified against code):
- src/v2/api_models/errors.py        : V2ErrorType (lines 9-14), V2ErrorResponse (23-28), V2FieldError (17-20)
- src/v2/auth.py                     : verify_token 401/503 conditions (25-46)
- src/v2/api.py                      : status codes & error_type usage:
                                       422 unsupported_url (786/792, 798/804, 2288/2294, 2299/2305)
                                       422 validation_error (1247/1260)
                                       502 provider_error  (890/895)
                                       500 pipeline_error   (901/906)
                                       503 pipeline_error (job store) (2312/2317, 2414/2418)
                                       404 not_found (2424/2428)
                                       202 accepted (POST /extract 2273)
                                       stale-job FAILED + pipeline_error (286 threshold=600.0, 2360-2399)
                                       health (4340-4386): components python/config/github_token, degraded logic
                                       rule_based discipline tagging runs for every repository regardless of runtime (1584-1609)
- src/v2/observation/github_rate_limit.py : GitHubRateLimitSummary (45-49), status rules (121-151), threshold=200 (31)
- src/v2/config.py                   : GME_GITHUB_TOKEN optional (64), MISSING_GME_GITHUB_TOKEN_ERROR (10)
- src/v2/ingest/providers/gimie_api_client.py : GIMIE_API_URL gating (32-37)
- src/v2/pipeline/stages/rule_based_disciplines.py : deterministic pulse:discipline via EPFL Graph RAG, "No LLM inference" (1-38)
- src/v2/pipeline/stages/concept_tagging.py   : optional stage (V2_CONCEPT_TAGGING_ENABLED), epfl_graph default backend; OpenAlex only in related-entity enrichment
- v2_router prefix = "/v2" (api.py:296)
-->

# Errors & troubleshooting

What the GME v2 API returns when something goes wrong, and how to fix it. Use this as a reference for HTTP status codes, the JSON error body, and the most common failure modes.

Base URL in examples is `http://localhost:8000`; the real deployment host is configurable. Most `/v2` endpoints require `Authorization: Bearer <token>` — only `GET /v2/health` is open. See the [V2 API Reference](v2-api-reference.md) for the full endpoint list and [Getting Started](getting-started.md) for setup.

## HTTP status codes

| Status | Meaning | When you'll see it |
|---|---|---|
| `200 OK` | Success | Synchronous `GET /v2/extract/{full_path}` returned a graph; `GET /v2/jobs/{id}` / `GET /v2/crawl/{id}` / `GET /v2/health` returned a record. A `200` extract response may still carry non-fatal `warnings` and empty optional fields. |
| `202 Accepted` | Job queued | `POST /v2/extract` accepted the job. The body has a `job_id` and `status_url` to poll. |
| `400 Bad Request` | Malformed request | Rare — for example a request body that isn't valid JSON before FastAPI can parse it. |
| `401 Unauthorized` | Auth failed | Missing or wrong `Authorization: Bearer` token on a protected endpoint. |
| `404 Not Found` | Resource missing | `GET /v2/jobs/{id}` / `GET /v2/crawl/{id}` for an unknown `job_id`; an unknown route. |
| `422 Unprocessable Entity` | Validation failed | The GitHub URL/handle is unsupported or malformed, request fields fail schema validation, or the extracted root entity fails strict validation. |
| `500 Internal Server Error` | Pipeline crash | The extraction pipeline raised an unhandled error mid-run. |
| `502 Bad Gateway` | Upstream provider unavailable | A required provider (e.g. GitHub) failed preflight during extraction. |
| `503 Service Unavailable` | Server not ready | `API_TOKEN` is unset on the server (auth fails closed), or the async job store is unavailable. |

!!! note "200 does not mean "complete""
    A successful extraction can come back with an empty `pulse:discipline`, an empty `keywords` list, or entries in `warnings`. These are *degradations*, not errors — see [Empty `pulse:discipline`](#faq-empty-discipline) below.

## The error body: `V2ErrorResponse`

Every `4xx`/`5xx` produced by the extraction routes returns the same JSON shape (fields that are null are omitted):

```json
{
  "error_type": "unsupported_url",
  "detail": "Not a GitHub URL or handle",
  "source_url": "https://gitlab.com/foo/bar",
  "detected_path_kind": "repo",
  "errors": [
    { "field": "schema:name", "message": "field is required", "value": null }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `error_type` | string enum | One of the [`V2ErrorType` values](#error-types) below. Stable — branch on this, not on `detail`. |
| `detail` | string | Human-readable explanation. Wording may change; do not parse it. |
| `source_url` | string \| omitted | The normalized URL the error is about, when known. |
| `detected_path_kind` | string \| omitted | What kind of path GME thought the URL was (e.g. `repo`, `user`, `org`) when the URL was rejected. |
| `errors` | array \| omitted | Per-field validation details (`field`, `message`, `value`) — populated for `validation_error`. |

!!! warning "Not every endpoint uses this shape"
    Auth (`401`/`503` from the bearer check) and some maintenance/index endpoints return FastAPI's plain `{"detail": "..."}` instead of the full `V2ErrorResponse`. Treat the presence of `error_type` as the signal that you got a structured extraction error.

## Error types {#error-types}

These are the only values `error_type` can take.

| `error_type` | Typical status | Meaning | How to resolve |
|---|---|---|---|
| `unsupported_url` | `422` | The path you submitted isn't a recognizable GitHub repo/user/org URL or handle, or it was malformed. | Pass a real GitHub URL (`https://github.com/<owner>/<repo>`) or bare handle. Check `detected_path_kind` for what GME parsed. |
| `validation_error` | `422` | Extraction ran, but the assembled root entity failed strict schema validation. | Inspect the `errors` array for the offending `field`/`message`. Usually the source repo is missing required metadata; fix the repo or accept the partial result. |
| `provider_error` | `502` | A required upstream provider (e.g. GitHub) was unavailable during preflight. | Retry later; check `GET /v2/health` for GitHub token/rate-limit status. |
| `pipeline_error` | `500` / `503` | The extraction pipeline raised an unhandled error, the async job store is unavailable, or a queued job's worker died mid-run (the job is then marked `failed`). | Retry. If it's a `503`, the job store/cache is disabled server-side — contact the operator. |
| `not_found` | `404` | No extract job exists for the `job_id` you polled. | Verify the `job_id` from the `POST /v2/extract` response; resubmit if the job has expired. |

---

## FAQ

Each entry is **symptom → cause → fix**.

### 401 Unauthorized on every protected call {#faq-401}

- **Symptom:** `401` with `{"detail": "Missing bearer token"}` or `{"detail": "Invalid bearer token"}`, and a `WWW-Authenticate: Bearer` response header.
- **Cause:** No `Authorization` header, a non-`Bearer` scheme, or a token that doesn't match the server's `API_TOKEN`.
- **Fix:** Send the header exactly as `Authorization: Bearer <token>`.

```bash
curl -s "http://localhost:8000/v2/extract/https://github.com/sdsc-ordes/gimie" \
  -H "Authorization: Bearer $API_TOKEN" | jq
```

!!! note "503 instead of 401?"
    If auth returns `503` with `{"detail": "Auth not configured: API_TOKEN is unset"}`, the *server* has no `API_TOKEN` set. It fails closed by design — no dev bypass. This is an operator fix, not a client one.

### 422 — "unsupported URL" {#faq-422-url}

- **Symptom:** `422` with `error_type: "unsupported_url"`.
- **Cause:** The path isn't a GitHub URL/handle, or it's structurally invalid. `detected_path_kind` shows what GME tried to interpret it as.
- **Fix:** Use a supported GitHub URL or bare `owner/repo` handle. Non-GitHub hosts (GitLab, etc.) are not accepted on this endpoint.

```bash
# Rejected: not a GitHub URL
curl -s "http://localhost:8000/v2/extract/https://gitlab.com/foo/bar" \
  -H "Authorization: Bearer $API_TOKEN"
```

```json
{
  "error_type": "unsupported_url",
  "detail": "Not a GitHub URL or handle",
  "source_url": "https://gitlab.com/foo/bar"
}
```

### 422 — "validation_error" with a field list {#faq-422-validation}

- **Symptom:** `422` with `error_type: "validation_error"` and a populated `errors` array.
- **Cause:** The pipeline produced a root entity that doesn't satisfy the strict Open Pulse schema (a required field missing or wrong type).
- **Fix:** Read each `errors[].field` / `errors[].message`. The fix is usually upstream (add the missing metadata to the repository, e.g. a `CITATION.cff` or description).

### 404 — repo seems to exist but I get "not found" {#faq-404}

- **Symptom:** `404` with `error_type: "not_found"`.
- **Cause:** On `GET /v2/jobs/{id}` and `GET /v2/crawl/{id}`, this means **no job with that `job_id`** — not a missing repository.
- **Fix:** Use the exact `job_id` from your `POST /v2/extract` response. Jobs are not retained forever; resubmit if it has expired.

!!! warning "Private or nonexistent GitHub repos"
    A bad/private/nonexistent *repository* (as opposed to a bad job id) does not surface as `not_found`. The URL may be accepted as well-formed and then fail downstream as a `provider_error` (`502`) or `pipeline_error` (`500`), depending on how GitHub responds. Make sure `GME_GITHUB_TOKEN` has access to the repo.

### My job has been "running" forever {#faq-stuck-jobs}

- **Symptom:** `GET /v2/jobs/{id}` or `GET /v2/crawl/{id}` keeps returning `status: "running"`.
- **Cause:** Big repos take a while (the pipeline makes many GitHub/provider calls). But a worker can also die mid-run (deploy, OOM, kill) without updating the record.
- **Fix:** Keep polling. The worker writes a periodic heartbeat (`last_heartbeat_at`). If no heartbeat lands for **over 600 seconds**, the next poll flips the job to `failed` with a `pipeline_error` so you stop waiting:

```json
{
  "job_id": "…",
  "status": "failed",
  "error": {
    "error_type": "pipeline_error",
    "detail": "extract job orphaned: worker process died mid-extraction (no heartbeat for 612s)"
  }
}
```

  When that happens, simply submit a fresh `POST /v2/extract`.

### `GET /v2/health` says "degraded" {#faq-health-degraded}

- **Symptom:** `GET /v2/health` returns `200` but `status: "degraded"` (or `unhealthy`).
- **Cause:** The overall status is the worst of the per-component statuses in `components`. The components are `python`, `config`, and `github_token`. The common degraded trigger is the GitHub token.
- **Fix:** Inspect `components` and `github_rate_limit` to find the culprit:

```bash
curl -s "http://localhost:8000/v2/health" | jq
```

```json
{
  "status": "degraded",
  "components": {
    "python": "healthy",
    "config": "healthy",
    "github_token": "degraded"
  },
  "version": "2.x.y",
  "github_rate_limit": { "status": "degraded", "total_remaining": 0, "tokens": [] }
}
```

  What each component means:

  | Component | `degraded` / `unhealthy` because… | Operator fix |
  |---|---|---|
  | `github_token` | `GME_GITHUB_TOKEN` (or `GME_GITHUB_TOKEN_POOL`) is unset → always `degraded`; or the live rate-limit probe came back degraded/unhealthy. | Set a valid `GME_GITHUB_TOKEN`. |
  | `config` | `V2Config` failed to load (`unhealthy`). | Fix the server environment. |
  | `python` | Runtime below the minimum supported Python (`unhealthy`). | Upgrade the runtime. |

!!! note "RCP_TOKEN and the gimie sidecar aren't health components"
    `RCP_TOKEN` (the LLM credential used for `llm`/`hybrid` runtimes and the optional concept-tagging `llm` backend) and `GIMIE_API_URL` (the optional `gimie-api` sidecar) are **not** reported by `GET /v2/health`. A missing `RCP_TOKEN` surfaces at request time as a `pipeline_error` / `provider_error` on `llm`/`hybrid` extractions (use `agent_runtime=rule_based` to avoid the LLM path). When `GIMIE_API_URL` is unset, GME silently falls back to in-process gimie — no error.

### GitHub rate limits {#faq-rate-limits}

- **Symptom:** Extractions slow down, fail, or `GET /v2/health` reports `github_token: "degraded"` with `github_rate_limit.total_remaining` near zero.
- **Cause:** Your GitHub token pool is exhausted or nearly so. The health probe marks the token `degraded` when no token has capacity, or when the combined remaining quota drops below 200; `unhealthy` when all tokens are invalid.
- **Fix:** Wait until `github_rate_limit.earliest_reset`, or have the operator add more tokens to `GME_GITHUB_TOKEN_POOL`. Each token's per-position status (`ok` / `rate_limited` / `invalid` / `unreachable`) is in `github_rate_limit.tokens` — token values are never exposed.

### Empty `pulse:discipline` (or empty keywords) {#faq-empty-discipline}

- **Symptom:** A `200` extraction whose `pulse:discipline` is empty or whose concept/keyword fields are sparse.
- **Cause:** Discipline tagging is a best-effort step that runs for **every** repository extraction, on all runtimes (including `rule_based`). It is a *deterministic* stage backed by the EPFL Graph disciplines RAG — no LLM inference is involved. It only stamps a discipline when a semantic match clears the score floor and resolves to an allowed ontology IRI, so a repo with weak/ambiguous signals (thin README, no description) legitimately yields an empty list. If the EPFL Graph RAG resources aren't available on the deployment, the field also degrades to empty rather than failing the request. (The separate, opt-in concept-tagging stage — `V2_CONCEPT_TAGGING_ENABLED`, which produces keywords/concepts and may call OpenAlex/EPFL Graph — is what populates the concept/keyword fields, and it likewise degrades to empty.)
- **Fix:** Add a clear `schema:description` and a substantive README to the repository so the discipline matcher has signal to work with. Switching `agent_runtime` is **not** required — disciplines are populated on `rule_based` too. Check the response's `warnings` array for messages from the discipline-tagging stage. An empty discipline is a valid, non-error outcome.

---

## See also

- [V2 API Reference](v2-api-reference.md) — every endpoint, request/response field, and auth requirement.
- [Getting Started](getting-started.md) — obtaining a bearer token and configuring the server.
