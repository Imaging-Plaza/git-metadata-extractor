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
- `force_refresh`: `true|false` (default `false`, bypasses provider cache)
- `include_intermediates`: `true|false` (default `false`, returns run-scoped intermediate envelopes for this extract run only)

Examples:

```bash
curl -s \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=jsonld" \
  | jq
```

```bash
curl -s \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=json&force_refresh=true" \
  | jq
```

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
- `reconciliation`
- `strict_validation`
- `output_assembly`
- `jsonld_build`
- `shacl_gate` (non-fatal warnings)
- `graph_write` (GraphStore upsert path)

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

## V2 Environment Variables

| Variable | Default | Notes |
| --- | --- | --- |
| `V2_GRAPH_DB_PATH` | `data/v2_graph.db` | SQLite graph-store path |
| `V2_INTERMEDIATE_HISTORY_LIMIT` | `5` | Max intermediates returned by extract-stage reads and per-agent graph response caps |
| `V2_ENABLE_LOGFIRE` | `true` | Enables v2 Logfire instrumentation |
| `V2_DISABLE_CACHE` | `false` | Bypass v1-backed provider cache for all v2 extract runs |
| `LOGFIRE_TOKEN` | unset | Optional Logfire token |
| `GITHUB_TOKEN` | unset | Required for healthy provider preflight |
