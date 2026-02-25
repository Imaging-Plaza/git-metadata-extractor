# V2 API Reference

This document describes the mounted v2 API surface under `/v2`.

## Endpoints

### `GET /v2/health`

Returns service readiness for v2 dependencies.

Example:

```bash
curl -s http://localhost:1234/v2/health | jq
```

### `GET /v2/extract/{full_path}`

Runs the v2 extraction pipeline for a GitHub URL path.

Query parameters:

- `output_format`: `jsonld` (default) or `json`
- `force_refresh`: `true|false` (default `false`, bypasses provider cache)
- `include_intermediates`: `true|false` (default `false`)

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

- `source_url`: optional source URL used for run-scoped filtering
- `entity_type`: repeatable filter (for example `entity_type=person&entity_type=repository`)
- `include_intermediates`: `true|false` (default `false`)

Example:

```bash
curl -s \
  "http://localhost:1234/v2/graph?source_url=https://github.com/octocat/Hello-World&entity_type=repository" \
  | jq
```

## Response Contracts

`/v2/extract` returns:

- `source_url`
- `detected_type`
- `output_format`
- `output`
- `warnings`
- `stats`
- optional `intermediates`

`/v2/graph` returns:

- `graph_jsonld` (must contain `@context` and `@graph`)
- `stats`
- optional `intermediates`

## V2 Environment Variables

| Variable | Default | Notes |
| --- | --- | --- |
| `V2_GRAPH_DB_PATH` | `data/v2_graph.db` | SQLite graph-store path |
| `V2_INTERMEDIATE_HISTORY_LIMIT` | `5` | Max intermediate snapshots returned |
| `V2_ENABLE_LOGFIRE` | `true` | Enables v2 Logfire instrumentation |
| `V2_DISABLE_CACHE` | `false` | Bypass v1-backed provider cache for all v2 extract runs |
| `LOGFIRE_TOKEN` | unset | Optional Logfire token |
| `GITHUB_TOKEN` | unset | Required for healthy provider preflight |
