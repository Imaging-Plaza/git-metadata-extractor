# Wire the GitLab index family into the HTTP API

**Date:** 2026-06-06
**Branch:** `feat/gitlab-api-endpoints`
**Goal:** Add `POST /v2/indices/<name>/ingest` + `POST /v2/indices/<name>/search` for all
**9** gitlab stores (`gitlab_{epfl,ethz,datascience}_{projects,groups,users}`), bringing
them to parity with the other indices. They are currently only in `/v2/manifest`.

## Pattern to mirror
- Search runner + ingest-job runner per index live in `git_metadata_extractor/indices/<name>.py`.
  Closest analog: `git_metadata_extractor/indices/renkulab.py` (instance-crawl ingest + entity search).
- Endpoints in `git_metadata_extractor/api.py`: ingest returns `IndexIngestJobAccepted` (202) and dispatches
  a background task that updates `IndexIngestJobStore`; search returns `IndexSearchResponse`
  via `_search_response_or_unavailable(await run_<name>_search(payload, app_state), index_name=...)`.
- `hit_from_raw` (`git_metadata_extractor/indices/_search_common.py`) normalises the leaf retrieval raw shape
  (`{id, vector_score, rerank_score, payload, entity}`) — the gitlab leaves already return this.
- Search request model `IndexSearchRequest` (query/top_k/candidate_k/filter_payload/target)
  already exists. Ingest request model is new (below).

## GitLab leaf entrypoints (already exist, all 9 uniform)
- `src.index.<name>.ingest.run_ingest(*, limit: int | None = None) -> dict[str,int]`
- `src.index.<name>.embed.run_embed(*, limit: int | None = None) -> dict[str,int]`
- `src.index.<name>.retrieval.search(query, *, top_k=10, candidate_k=50, filter_payload=None) -> list[dict]`
All synchronous → wrap in `asyncio.to_thread`. Single-entity stores (ignore `target`).

## Work

### 1. `git_metadata_extractor/api_models/contracts.py` — new ingest request
```python
class GitLabIngestRequest(BaseModel):
    """Body for POST /v2/indices/gitlab_*/ingest. Full public-instance crawl;
    `limit` optionally caps how many records are ingested (smoke tests / first run)."""
    model_config = ConfigDict(extra="forbid")
    limit: int | None = Field(default=None, ge=1, le=1_000_000,
        description="Optional cap on records ingested this run; omit to crawl the whole instance.")
```
Export it from the `api_models` package the same way the other `*IngestRequest` models are.

### 2. `git_metadata_extractor/indices/gitlab.py` — generic runners (NEW)
- `GITLAB_INDEX_NAMES: list[str]` = the 9 store names.
- `async def run_gitlab_ingest_job(*, index_name, payload: GitLabIngestRequest, app_state, job_store, job_id) -> None`
  - Mirror `run_renkulab_ingest_job`: set RUNNING; import `src.index.<index_name>.ingest`
    + `.embed` inside try (ImportError → FAILED "gitlab index module unavailable");
    `ingested = await asyncio.to_thread(run_ingest, limit=payload.limit)`;
    `embedded = await asyncio.to_thread(run_embed)`; COMPLETED with
    `summary={"index": index_name, "ingested": ingested, "embedded": embedded}`.
    Wrap everything so the job is always marked FAILED with `error=str(exc)` on exception.
- `async def run_gitlab_search(index_name, payload: IndexSearchRequest, app_state) -> IndexSearchResponse | None`
  - import `src.index.<index_name>.retrieval.search` (ImportError → return None);
    `raw = await asyncio.to_thread(search, payload.query, top_k=payload.top_k,
    candidate_k=payload.candidate_k or max(payload.top_k*5, 50), filter_payload=payload.filter_payload)`;
    return `IndexSearchResponse(index_name=index_name, target=payload.target,
    query=payload.query, hits=[hit_from_raw(h) for h in raw])`.
- Validate `index_name in GITLAB_INDEX_NAMES` (defensive) — raise/return None otherwise.
- `__all__` exports.

### 3. `git_metadata_extractor/api.py` — register 18 routes
Register via a **loop + factory** over `GITLAB_INDEX_NAMES` (18 explicit handlers would be
excessive boilerplate). For each name build two async endpoint handlers with proper
annotations and register with `v2_router.add_api_route(...)`:
- ingest: path `/indices/<name>/ingest`, methods `["POST"]`, `response_model=IndexIngestJobAccepted`,
  `response_model_exclude_none=True`, `status_code=202`, `tags=["Indices"]`,
  `name=f"{name}_ingest_post"`, summary mentioning the store. Handler mirrors
  `github_users_ingest_post` body but calls `run_gitlab_ingest_job(index_name=name, ...)`.
- search: path `/indices/<name>/search`, `response_model=IndexSearchResponse`,
  `response_model_exclude_none=True`, `tags=["Indices"]`, `name=f"{name}_search_post"`,
  handler calls `_search_response_or_unavailable(await run_gitlab_search(name, payload, request.app.state), index_name=name)`.
The factory closures MUST keep the `payload`/`request`/`_token=Depends(verify_token)` signature
so FastAPI parses the body + enforces auth (see `github_users_search_post`). Bind `name` via
default-arg or closure factory to avoid the late-binding loop bug.

### 4. Tests `tests/v2/test_indices_gitlab_endpoints.py` (NEW)
Mirror `tests/v2/test_api_manifest.py` app/client harness (`FastAPI(); include_router(v2_router)`,
bearer `test-api-token`, `ASGITransport`/`AsyncClient`). Cover:
- All 9 `/ingest` and 9 `/search` routes are registered (introspect `app.routes` paths).
- A `/search` returns 200 with normalized hits when the leaf `search` is monkeypatched to
  return one fake raw hit (patch `src.index.gitlab_epfl_users.retrieval.search` — NO real
  Qdrant/RCP). Assert the response `index_name`, `query`, and one hit `id`.
- `/search` and `/ingest` require auth (401/403 without bearer).
- `/ingest` without a provider cache returns 503 (the bare test app has none) — matches the
  existing ingest-endpoint behaviour. (Check how existing ingest tests assert this and match.)

## Gate
`tests/v2/` (full) + `tests/index/` green. No real network/LLM/Qdrant in tests.
House note: gitlab leaf configs use `Optional` like siblings (pre-existing UP045; ruff not a CI gate).
