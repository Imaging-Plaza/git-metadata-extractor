# Migration Guide: V1 to V2

This guide describes how to run v1 and v2 side by side and migrate clients incrementally.

## Coexistence Model

- v1 endpoints remain mounted under `/v1/...`.
- v2 endpoints are mounted under `/v2/...`.
- v2 does not require v1 route changes and can be adopted per client.

## Endpoint Mapping

| V1 usage | V2 equivalent |
| --- | --- |
| `GET /v1/repository/llm/json-ld/{full_path}` | `GET /v2/extract/{full_path}?output_format=jsonld` |
| `GET /v1/repository/llm/json/{full_path}` | `GET /v2/extract/{full_path}?output_format=json` |
| `GET /v1/repository/gimie/json-ld/{full_path}` | `GET /v2/extract/{full_path}?output_format=jsonld` |
| n/a | `GET /v2/graph` |
| n/a | `GET /v2/health` |

## Copy-Paste Migration Examples

### Repository JSON-LD extraction

V1:

```bash
curl -s \
  "http://localhost:1234/v1/repository/llm/json-ld/https://github.com/octocat/Hello-World" \
  | jq
```

V2:

```bash
curl -s \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=jsonld" \
  | jq
```

### Repository JSON extraction

V1:

```bash
curl -s \
  "http://localhost:1234/v1/repository/llm/json/https://github.com/octocat/Hello-World" \
  | jq
```

V2:

```bash
curl -s \
  "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=json" \
  | jq
```

## Response Format Differences

- v1 repository endpoints return `APIOutput` (`link`, `output`, `cached`).
- v2 extract returns `V2ExtractResponse` (`source_url`, `detected_type`, `output_format`, `output`, `warnings`, `stats`, optional `intermediates`).
- v2 graph export is explicit via `/v2/graph` and returns `graph_jsonld` + `stats`.

## Graph Store Setup (V2)

1. Set `V2_GRAPH_DB_PATH` (or accept default `data/v2_graph.db`).
2. Run v2 extract endpoints to populate run metadata and graph entities.
3. Use `/v2/graph` for filtered JSON-LD export.

## New/Relevant V2 Environment Variables

- `V2_GRAPH_DB_PATH` (default `data/v2_graph.db`)
- `V2_INTERMEDIATE_HISTORY_LIMIT` (default `5`)
- `V2_ENABLE_LOGFIRE` (default `true`)
- `LOGFIRE_TOKEN` (optional)
- `GITHUB_TOKEN` (required for healthy provider preflight)

## Breaking/Behavior Changes

- V2 response envelopes are not shape-identical to v1 responses.
- V2 introduces run-scoped stats and optional intermediates payloads.
- V2 canonicalization and reconciliation pipelines are stricter than v1 heuristics.

## Deprecation Timeline

- March 31, 2026: v1 marked as deprecated in docs and release notes.
- June 30, 2026: v1 feature freeze; only critical bug fixes.
- September 30, 2026 (target `v3.0.0`): v1 endpoints removed.

If timeline dates move, update this document and `CHANGELOG.md` in the same release.
