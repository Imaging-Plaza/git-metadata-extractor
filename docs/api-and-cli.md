# API and CLI

## Main entrypoints

- API app: `src/api.py`
- Repository analysis orchestrator: `src/analysis/repositories.py`
- User analysis orchestrator: `src/analysis/user.py`
- Organization analysis orchestrator: `src/analysis/organization.py`

## Active API endpoints

- `GET /v1/repository/gimie/json-ld/{full_path:path}`: GIMIE-only JSON-LD extraction.
- `GET /v1/repository/llm/json/{full_path:path}`: repository Pydantic output.
- `GET /v1/repository/llm/json-ld/{full_path:path}`: repository JSON-LD output.
- `GET /v1/user/llm/json/{full_path:path}`: user profile analysis.
- `GET /v1/org/llm/json/{full_path:path}`: organization analysis.
- Cache management endpoints under `/v1/cache/*`.

Common query flags:

- `force_refresh=true`: bypass cache.
- `enrich_orgs=true`: run organization enrichment.
- `enrich_users=true`: run user enrichment (repository/user routes).

## Response shape

All analysis endpoints return `APIOutput` (`src/data_models/api.py`):

- `link`
- `type` (`repository`, `user`, `organization`)
- `parsedTimestamp`
- `output` (model object or JSON-LD dict)
- `stats` (`APIStats` token usage, duration, GitHub rate-limit headers)

## Endpoint-to-pipeline map

```mermaid
flowchart LR
    A[/repository/llm/*] --> R[Repository.run_analysis]
    B[/user/llm/json/*] --> U[User.run_analysis]
    C[/org/llm/json/*] --> O[Organization.run_analysis]

    R --> RA[Atomic repository pipeline + optional enrichments]
    U --> UA[GitHub parse + user/org enrich + linked entities + EPFL]
    O --> OA[Atomic organization pipeline]
```

## Run the API

```bash
just serve-dev
```

## Smoke tests

```bash
just api-test-gimie
just api-test-extract
just api-test-extract-refresh
```

## CLI status and alternatives

- `just extract ...` currently calls `src/main.py`, which still imports legacy `core.*` modules.
- Use API endpoints for full extraction flows.
- Use `scripts/convert_json_jsonld.py` for JSON <-> JSON-LD conversion workflows.
