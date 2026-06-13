# Bug 04 — Index-ingest endpoints starve extraction (no load isolation)
**Severity:** medium · **Status:** ◐ Partially fixed (2026-06-12) — Option B core + D shipped · **Area:** API / scheduling / load isolation

## Resolution (2026-06-12) — Option B (core) + Option D

Shipped the bounded ingest pool for the dominant thread-pool-starvation axis:
- New `src/v2/indices/_ingest_pool.py`: a bounded, ingest-only `ThreadPoolExecutor`
  (`V2_INGEST_MAX_THREADS`, default 2) + `run_in_ingest_pool()` (drop-in for
  `asyncio.to_thread`) + `reset_ingest_pool()` for tests.
- Routed the **shared heavy ingest steps** through it: `_embed_step.py` (embed pass,
  WAL checkpoint, `.ro` snapshot — used by every provider) and `gitlab.py` (ingest +
  embed). These are where bulk ingest holds threads for sustained periods, so the
  default pool now stays free for extraction.
- Option D: documented the residual constraint in `docs/OPERATIONS_RUNBOOK.md`
  (don't run large bulk ingests concurrently with latency-sensitive extraction on a
  1–2 worker box).
- Tests: `tests/v2/test_ingest_pool.py` — named dedicated thread, arg/kwarg forwarding,
  bounded concurrency (≤ cap), and the isolation property (a saturated ingest pool does
  not delay a default-pool `asyncio.to_thread`). 51 ingest/embed/gitlab regression tests pass.

**Deliberately deferred** (still on the default pool):
- The lighter per-entity ingest *fetch* loops (`result = await asyncio.to_thread(_ingest_one_*)`)
  in the ~14 provider modules — sequential per job and far lighter than the embed pass.
  Route them through `run_in_ingest_pool` too if profiling shows fetch-side contention.
- Option A (separate `gme-ingest-worker` process) — the durable fix; also the SQLite
  cache writer-lock axis (Consequence #3) is untouched.

## Symptom
Running `POST /v2/indices/<name>/ingest` concurrently with `POST /v2/extract` on the
single-worker production server (`WORKERS=2` in `tools/deploy/docker-compose.yml:33`, but
operators frequently run the documented single-worker `just serve-single` →
`uvicorn ... --workers 1`, `justfile:55`) caused:
- TCP connection resets on in-flight `/extract` requests, and
- collapsed extraction throughput (~34 s/entity vs. baseline).

The server has no mechanism to isolate bulk-ingest load from interactive extraction:
both classes of work are accepted, scheduled, and executed **in the same process, on the
same asyncio event loop, drawing from the same default thread pool, and writing to the
same SQLite cache file**. A burst of ingest work therefore degrades extraction.

## Findings (execution model for ingest vs extract, file:line)

### Both endpoints are fire-and-forget background tasks on the *same* event loop
- `POST /v2/extract` (`src/v2/api.py:2242` `extract_post`) builds a job record and does
  `asyncio.create_task(_run_extract_job(...))` at `src/v2/api.py:2301`, then tracks it via
  `_track_background_task` / `_register_job_task` (`src/v2/api.py:469`, `:483`). It returns
  `202 Accepted` immediately.
- Every `POST /v2/indices/<name>/ingest` does the identical thing. Representative sites:
  - zenodo: `asyncio.create_task(run_zenodo_records_ingest_job(...))` at `src/v2/api.py:2596`
  - the five HF entity endpoints share `_hf_entity_ingest_post` →
    `asyncio.create_task(runner(...))` at `src/v2/api.py:2650`
  - github_repos: `src/v2/api.py:2811`; github_users: `:2858`; github_organizations: `:2909`;
    huggingface_papers: `:2962`; openalex: `:3009`; orcid: `:3052`; renkulab: `:3095`;
    swissubase: `:3141`; ethz_research_collection: `:3188`; oamonitor: `:3236`;
    dockerhub: `:3283`; the nine gitlab stores via `_make_gitlab_ingest_handler` →
    `src/v2/api.py:3917`.
  All call `_track_background_task` (`src/v2/api.py:469`) — the **same** task set/event loop
  that extract jobs live on.

**Consequence #1 (event-loop sharing):** extract jobs and ingest jobs are coroutines
scheduled on one event loop inside one uvicorn worker. There is no separate process, no
separate loop, and no priority between the two task classes — `asyncio` schedules them
round-robin by readiness. `--workers 1` means literally one loop for everything.

### The heavy ingest work is offloaded to threads — but to the *shared default* pool
The ingest runners do **not** block the event loop directly; the CPU/IO-heavy steps are
wrapped in `asyncio.to_thread(...)`:
- zenodo fetch step: `await asyncio.to_thread(ingest_by_ids, ...)` at
  `src/v2/indices/zenodo_records.py:115`
- github_repos fetch step: per-repo `await asyncio.to_thread(_ingest_one_repo, ...)` loop at
  `src/v2/indices/github_repos.py:107`
- the embed half for every provider: `run_embed_step` →
  `await asyncio.to_thread(embed_call)` at `src/v2/indices/_embed_step.py:150`, plus a
  `CHECKPOINT` (`_embed_step.py:92`) and a snapshot copy (`_embed_step.py:116`), all on threads.

The problem is **which** thread pool. `asyncio.to_thread` dispatches to the loop's *default*
`ThreadPoolExecutor` (`loop.run_in_executor(None, ...)`). Nothing in `src/v2/` ever calls
`loop.set_default_executor(...)` or sizes a dedicated pool — `grep` for
`set_default_executor` / `ThreadPoolExecutor(max_workers` in `src/v2/` returns only two
*provider-local* `max_workers=1` pools (`src/v2/ingest/providers/base.py:45`,
`infoscience_provider.py:35`) that are unrelated to the API offload path. So the default
pool is used everywhere.

On the production host the default pool is sized `min(32, os.cpu_count()+4)`. On a 32-core
box that is 32 threads — but the extraction pipeline **also** offloads many blocking calls
to that same pool (e.g. `rule_based_disciplines` `asyncio.to_thread` at
`src/v2/pipeline/stages/rule_based_disciplines.py:291`, concept-tagging `:655/:664/:678`,
the RAG provider fetches under `src/v2/ingest/providers/*_rag.py`, link-veracity, etc.), and
the orchestrator fans those out up to `V2_MAX_CONCURRENT_AGENTS` (default 8,
`src/v2/api.py:403`) *per extract request*. A bulk ingest of N repos issues N sequential
`to_thread` fetches plus a heavy embed pass; concurrent ingests multiply that. Under enough
ingest pressure the shared pool's worker threads are all occupied by ingest fetch/embed work,
so extract's offloaded calls queue behind them — the offload that was supposed to keep the
loop free instead **serializes extraction behind ingest**.

**Consequence #2 (thread-pool sharing):** ingest and extract compete for the same finite
default `ThreadPoolExecutor`. The pool is the real scarce resource, not the loop.

### Third shared resource: the single SQLite job/cache file
Both job stores write to **one** `ProviderCache` SQLite file:
- extract job records: `JobStore` namespace `v2-extract-job` (`src/v2/jobs.py:14`)
- ingest job records: `IndexIngestJobStore` namespace `v2-index-ingest-job`
  (`src/v2/indices/jobs.py:13`)
- the same cache also backs every provider response read/write during extraction.

`ProviderCache` opens a **fresh connection per call** with a 30 s busy timeout
(`src/v2/ingest/cache.py:79`) in WAL mode (`:80`). WAL allows readers during a write but
still **serializes writers**. The extract heartbeat writes a job record every
`_JOB_HEARTBEAT_INTERVAL_SECONDS` (`src/v2/api.py:555`), provider responses are cached
constantly, and ingest jobs write status transitions plus large `summary` blobs. Under
heavy ingest these `INSERT OR REPLACE` writes contend on the single writer lock; a slow
write can stall extract's cache reads/writes up to the 30 s timeout, which is consistent
with both the throughput collapse and the connection resets (the client gives up while the
worker is blocked).

**Consequence #3 (storage sharing):** a single SQLite writer lock couples ingest write
bursts to extraction's cache traffic. (This overlaps Bug 03's memory story but the lock is
a distinct axis — see cross-refs.)

### What is *not* the cause
The DuckDB index stores are per-provider files held as long-lived writer connections on
`app.state` (`src/v2/indices/zenodo_records.py:55`, etc.) and the WAL/checkpoint machinery
in `_embed_step.py`. Those cause the access-mode conflict tracked in **Bug 01**, not the
extract starvation here — extract does not open the index DuckDB writers. The starvation is
loop/pool/SQLite-lock contention, not DuckDB-file contention.

## Root cause
Bulk index ingestion is a long-running, fan-out, IO+CPU-heavy operation that is served
**in-process with no isolation from interactive extraction**. Specifically:
1. Ingest jobs are scheduled as `asyncio.create_task` on the *same* event loop as extract
   (`src/v2/api.py:2596` et al. vs `:2301`), inside one uvicorn worker.
2. Their heavy work is offloaded to the **shared default `ThreadPoolExecutor`** (via
   `asyncio.to_thread`, e.g. `src/v2/indices/_embed_step.py:150`,
   `src/v2/indices/github_repos.py:107`), the same pool extraction relies on — so ingest
   saturates the pool and extract's offloaded calls queue behind it.
3. Both write to one SQLite `ProviderCache` (`src/v2/ingest/cache.py:79`), whose single
   writer lock couples ingest write bursts to extraction's cache traffic.
With only 1–2 uvicorn workers there is no spare capacity to absorb the bulk load, so ingest
monopolizes the shared resources and extraction throughput collapses with client resets.

## Proposed fix (options + recommendation)

### Option A — Move ingest to a separate process/service (strongest isolation) — RECOMMENDED long-term
Run index ingestion as its **own process** so it cannot touch the API worker's event loop or
thread pool at all.
- Concrete shape: add a `gme-ingest-worker` service to `tools/deploy/docker-compose.yml`
  (alongside `gme-api`, `gme-qdrant`, `gme-gimie-api`). The API `POST /v2/indices/*/ingest`
  handlers stop calling `asyncio.create_task(run_*_ingest_job)` and instead enqueue the job
  onto a shared broker (Redis/RQ, or a DB-backed queue table in the existing SQLite/DuckDB).
  The worker process pops jobs and runs the existing `run_*_ingest_job` coroutines.
- File/function targets: the ~25 `asyncio.create_task(run_*_ingest_job(...))` call sites in
  `src/v2/api.py` (zenodo `:2596`, `_hf_entity_ingest_post` `:2650`, github_repos `:2811`,
  github_users `:2858`, github_organizations `:2909`, huggingface_papers `:2962`, openalex
  `:3009`, orcid `:3052`, renkulab `:3095`, swissubase `:3141`, ethz `:3188`, oamonitor
  `:3236`, dockerhub `:3283`, gitlab `_make_gitlab_ingest_handler` `:3917`) → replace the
  `create_task` with `queue.enqueue(...)`. Add an entrypoint module
  `src/v2/indices/worker.py` that consumes the queue and invokes the unchanged runners. The
  per-provider DuckDB writer stores already live on `app.state`, so the worker gets its own
  writer handle (which also sidesteps Bug 01's same-process access-mode conflict).
- Trade-offs: best isolation and the only option that survives a horizontal API scale-out;
  cost is a new broker dependency, a second deployable, and the index DuckDB files must be on
  a shared volume (already `gme-data` in compose) — coordinate writer ownership so the worker,
  not the API, holds the write connection.

### Option B — Dedicated bounded thread pool for ingest (cheapest real isolation) — RECOMMENDED short-term
Keep ingest in-process but stop it from sharing extraction's thread pool.
- Concrete shape: create one module-level `ingest_executor = ThreadPoolExecutor(max_workers=K)`
  (small, e.g. 2) owned by the ingest layer, and route every ingest offload through it via
  `loop.run_in_executor(ingest_executor, fn)` instead of `asyncio.to_thread`. That caps how
  many threads ingest can ever hold, leaving the default pool for extraction.
- File/function targets: `run_embed_step` (`src/v2/indices/_embed_step.py:150`, `:92`, `:116`)
  and each runner's fetch offload (`src/v2/indices/zenodo_records.py:115`,
  `github_repos.py:107`, `gitlab.py:77-78`, and the analogous `to_thread` sites in the other
  index modules). Optionally also bump the API's *default* pool deliberately so its size is
  no longer accidental.
- Trade-offs: small, surgical, no new infra, immediately decouples the thread-pool axis
  (Consequence #2). Does **not** isolate the event loop or the SQLite writer lock, and a
  single-worker API still interleaves loop time — pair with Option D.

### Option C — Priority / admission control favouring extraction
Add an `asyncio.Semaphore`-based admission gate so ingest yields to extraction.
- Concrete shape: a module-level `ingest_admission = asyncio.Semaphore(1)` (or a small N) that
  each `run_*_ingest_job` must acquire before doing fetch/embed work; size extraction's
  concurrency higher. Optionally reject/queue ingest with `503 + Retry-After` when extraction
  load is high (track in-flight extract jobs via the existing `_v2_job_task_by_id` registry,
  `src/v2/api.py:485`).
- File/function targets: wrap the bodies of the `run_*_ingest_job` coroutines (or add the
  acquire inside `run_embed_step`, `src/v2/indices/_embed_step.py`); read in-flight extract
  count from `request.app.state._v2_job_task_by_id`.
- Trade-offs: directly encodes "extraction wins", low infra cost; but a semaphore in the same
  process still shares the loop and pool, so it caps *concurrency* not *resource ownership* —
  weaker than A/B, best as a complement.

### Option D — Minimum bar: keep async-offload, document operational constraint
At minimum, ensure no ingest step ever runs *inline* on the loop (already true today — all
heavy work is under `asyncio.to_thread`), and document in `docs/OPERATIONS_RUNBOOK.md` that
bulk ingest must not run concurrently with latency-sensitive extraction on a 1–2 worker
deployment; schedule ingest in maintenance windows or against a separate API replica.
- Trade-offs: zero code risk, but it is a process workaround, not a fix — relies on operator
  discipline and does nothing for accidental concurrency.

### Recommendation
Ship **Option B + Option D** now (bounded ingest pool + runbook note) — small, low-risk, kills
the dominant thread-pool-starvation axis immediately. Plan **Option A** (separate ingest
worker/service) as the durable fix, since it is the only one that fully isolates loop, pool,
*and* gives the index DuckDB writers a single owner (also resolving Bug 01's premise). Add
**Option C**'s admission semaphore if profiling shows residual loop interleaving after B.

## Risks & considerations
- **DuckDB writer ownership (ties to Bug 01).** If ingest moves to a separate process
  (Option A), the API must relinquish the per-provider DuckDB *write* connection or the two
  processes hit DuckDB's single-writer lock. The `.ro.duckdb` snapshot path
  (`src/v2/indices/_embed_step.py:116`, `src/index/_snapshot.py`) already exists for exactly
  this read/write split — lean on it.
- **SQLite job-store contention (Consequence #3) is not fixed by A or B.** Both still write
  job/cache records to the one `ProviderCache` file. If the worker is a separate process it
  must reach the same SQLite file or the API can't report ingest status; consider moving job
  status to the broker, or accept the WAL writer-lock coupling and monitor the 30 s timeout
  at `src/v2/ingest/cache.py:79`.
- **Bounded pool sizing (Option B).** Too small a `K` slows ingest a lot (it is already
  largely sequential per job); too large reraises starvation. Make `K` an env var.
- **Admission rejection UX (Option C).** Returning `503` on ingest changes the API contract;
  prefer queue-and-defer over hard reject so callers' existing poll loops still work.
- **No regression to the auto-ingest hooks.** `extract` itself schedules best-effort
  auto-ingests on the same loop (`src/v2/api.py:1786`, `:1956` etc., via `asyncio.to_thread`
  at `:1943`). Whatever isolation is chosen for the explicit ingest endpoints should also
  cover these, or they reintroduce the same starvation from inside an extract.

## Test / verification plan
1. **Repro / regression load test.** Script that drives `POST /v2/extract` at a fixed rate and
   measures p50/p95 latency and per-entity time, first alone (baseline), then while firing a
   bulk `POST /v2/indices/github_repos/ingest` (many repos). Assert extract p95 stays within
   an agreed multiple of baseline and that no connection resets occur. Run against
   `just serve-single` (1 worker) to match the worst case.
2. **Thread-pool isolation unit test (Option B).** Patch the ingest offload to a slow
   sentinel and assert that an extraction-path `asyncio.to_thread` call still completes
   promptly (i.e. is not queued behind ingest) — verifies the default pool is no longer shared.
3. **Admission test (Option C, if implemented).** Simulate in-flight extract jobs in
   `_v2_job_task_by_id` and assert the ingest runner blocks/defers on the semaphore.
4. **SQLite contention probe.** Concurrent writers against `ProviderCache` (`cache.py`) under
   load; assert no `database is locked` errors and writes stay under the 30 s timeout.
5. **End-to-end on the deploy stack.** `docker compose -f tools/deploy/docker-compose.yml up`,
   then run test 1 against the container with `WORKERS=2`; confirm the fix holds with the new
   `gme-ingest-worker` service (Option A) and that ingest still completes + reports status.
6. **No-regression on existing suites.** `just test` (and the v2 API tests under `tests/`)
   plus a manual `POST /v2/extract` to confirm the response contract is unchanged.

## Effort estimate
- Option D (runbook only): ~0.5 day.
- Option B (bounded ingest pool, ~25 offload sites routed through it, env knob, tests): ~1.5–2 days.
- Option C (admission semaphore + tests): ~1 day.
- Option A (separate ingest worker/service: broker, worker entrypoint, requeue all ~25
  endpoints, DuckDB writer-ownership handoff, compose service, status plumbing, load tests):
  ~4–6 days.
- Recommended near-term bundle (B + D): ~2–2.5 days. Confidence the *mechanism* is correctly
  identified: high; confidence on exact production numbers (the ~34 s figure) without a repro
  harness: medium.

## Open questions
1. Which axis dominated in the observed incident — thread-pool saturation (Consequence #2),
   event-loop interleaving (#1), or the SQLite writer lock (#3)? A profiled repro (test 1 +
   pool/loop instrumentation) should disambiguate before committing to Option A vs B.
2. Production worker count of record: compose sets `WORKERS=2` (`docker-compose.yml:33`) but
   the runbook/`justfile:55` documents single-worker; the fix's urgency depends on which is real.
3. Is a broker (Redis) acceptable in the deploy topology, or should the separate worker share
   the existing SQLite/DuckDB-on-volume for the queue to avoid a new dependency?
4. Should the `extract`-triggered auto-ingest hooks (`src/v2/api.py:1786`+) be routed through
   the same isolated path, or are they low-volume enough to leave inline?
5. Acceptable extract-latency SLO during a concurrent ingest — this sets the pass/fail bar for
   test 1 and the sizing of Options B/C.
