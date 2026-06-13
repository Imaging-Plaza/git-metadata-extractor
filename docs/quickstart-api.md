<!--
End-user quickstart for API consumers. Verified against:
- src/v2/api.py: POST /v2/extract (extract_post), GET /v2/jobs/{id} (extract_job),
  GET /v2/extract/{full_path} (extract), GET /v2/health (open)
- src/v2/auth.py: Bearer token vs API_TOKEN env
- src/v2/api_models/contracts.py: V2ExtractRequest, V2ExtractJobAccepted, V2ExtractJob
Deeper pages: extracting-metadata.md, extraction-output.md, searching-indices.md, troubleshooting.md
-->

# Quickstart (API consumers)

Get from an API token to your first extracted repository in five minutes. This is
the happy path; each step links to the page with the full detail.

!!! note "Base URL & token"
    Examples use `http://localhost:8000` — substitute your deployment host. Set your
    bearer token (the server's `API_TOKEN`, handed to you by the operator) once:

    ```bash
    export GME=http://localhost:8000
    export API_TOKEN="paste-the-value-your-operator-gave-you"
    ```

## 1. Check the service is up (no token needed)

```bash
curl -s "$GME/v2/health" | jq '.status'
# "healthy"  (or "degraded" — see Troubleshooting)
```

`GET /v2/health` is the only open endpoint; everything below needs
`Authorization: Bearer $API_TOKEN`. → [Authentication details](extracting-metadata.md#authenticate)

## 2. Submit an extraction (async)

`POST /v2/extract` returns immediately with `202` and a `job_id` — the right choice
for `llm`/`hybrid` runs, which take seconds to minutes.

```bash
curl -s -X POST "$GME/v2/extract" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_url": "github.com/octocat/Hello-World", "agent_runtime": "rule_based"}'
# → {"job_id":"3f2c…","status":"pending","status_url":"/v2/jobs/3f2c…"}
```

A repository, user (`github.com/<name>`), or organization (`github.com/orgs/<name>`)
URL is accepted. → [All request fields](extracting-metadata.md#request-body)

## 3. Poll for the result

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" "$GME/v2/jobs/3f2c…" | jq '.status'
# pending → running → completed   (or failed / cancelled)
```

When `status` is `completed`, the job record carries the result. The lifecycle and
how stuck jobs are auto-failed are in [Extracting metadata](extracting-metadata.md#long-running-jobs-and-timeouts).

## 4. Or go synchronous for quick lookups

For fast `rule_based` runs you can skip the job and block on a single `GET`:

```bash
curl -s -H "Authorization: Bearer $API_TOKEN" \
  "$GME/v2/extract/github.com/octocat/Hello-World?agent_runtime=rule_based&output_format=jsonld" | jq
```

## 5. Read the output

The result is Open Pulse metadata — a JSON-LD `@graph` of typed entities
(`schema:Person`, `schema:SoftwareSourceCode`, `org:Organization`, …) or a flat
`json` envelope. → [Understanding extraction output](extraction-output.md)

## Where to go next

| You want to… | Page |
|---|---|
| Tune the request (runtime, format, internal fields, model override) | [Extracting metadata](extracting-metadata.md) |
| Understand the response entities and fields | [Understanding extraction output](extraction-output.md) |
| Search the RAG indices instead of extracting | [Searching the RAG indices](searching-indices.md) |
| Decode an error or a `degraded` health status | [Errors & troubleshooting](troubleshooting.md) |

!!! tip "No SDK yet"
    There is no official client library — interaction is plain HTTP. For batch runs,
    see `scripts/v2/batch_extract.sh` in the repository.
