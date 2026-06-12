# Bug 02 — Extraction server destabilizes under concurrent hybrid jobs

**Severity:** medium · **Status:** Investigated — plan ready (no code changed) · **Area:** HTTP / async job layer

> Confidence note: the *mechanism* below (blocking sync I/O on the event loop) is
> grounded in the code with file:line citations and is high-confidence. The exact
> mapping from that mechanism to the *specific* client-visible errors
> (`Connection reset by peer` / `Server disconnected without sending a response`)
> is a reasoned hypothesis — it has not been reproduced under instrumentation in
> this investigation. Treat the root-cause ranking as "most likely", not proven.

---

## Symptom

Running the pipeline with `max_workers > 1` and `runtime=hybrid` (LLM + rule-based),
the client driving `/v2/extract` sees repeated:

- `Connection reset by peer`
- `Server disconnected without sending a response`

while polling extract jobs, and **no throughput gain** vs. serial — i.e. hybrid is
effectively serial-only no matter how many concurrent jobs the client launches.
This is the HTTP/job layer, distinct from the DuckDB write-contention issue
(Bug #1) and from load-isolation (Bug #4).

Note: "`max_workers`" here is a **client-side** concurrency knob (how many extract
jobs the batch driver submits and polls in parallel). It is *not* the same as the
server's uvicorn/gunicorn `--workers`. The two interact badly, as explained below.

---

## Findings (how jobs are submitted / executed / polled, with file:line)

### Job submission — `POST /v2/extract`
- `src/v2/api.py:2242` `extract_post(...)` validates the URL, writes a `PENDING`
  job record to the `JobStore`, then schedules the work with
  **`asyncio.create_task(_run_extract_job(...))`** at `src/v2/api.py:2301`, tracks
  it (`_track_background_task`, `src/v2/api.py:469`) and indexes it by id
  (`_register_job_task`, `src/v2/api.py:483`). Returns `202 Accepted` immediately.
- Key point: the job runs **as a coroutine on the same event loop** that serves
  every HTTP request. There is no process pool, no separate worker process, no
  queue/broker — the "background task" is a task on the request loop.

### Job execution — `_run_extract_job` → `extract`
- `src/v2/api.py:506` `_run_extract_job(...)` flips the record to `RUNNING`, starts
  a 30s heartbeat task (`src/v2/api.py:540`, `_JOB_HEARTBEAT_INTERVAL_SECONDS`),
  then **`await extract(...)`** at `src/v2/api.py:557` — i.e. it calls the same
  GET-handler coroutine in-process.
- `extract(...)` (`src/v2/api.py:704`) runs the orchestrator
  (`orchestrator.execute(...)`, `src/v2/api.py:846`) and then a long chain of
  `await ...` stages.
- The hybrid refiner stage is gated here:
  `if resolved_runtime == AgentRuntime.HYBRID and _hybrid_refiner_is_enabled():`
  → `await run_refine_with_llm_stage(...)` at `src/v2/api.py:1127`–`1136`. This is
  the stage that only runs under hybrid and is the prime suspect for the
  hybrid-specific regression.

### The blocking work that is NOT offloaded (root of the instability)
The pipeline is `async` end-to-end, but several **hot-path stages call synchronous,
blocking I/O directly on the event loop** instead of via `asyncio.to_thread`:

1. **Context gather → gimie sidecar + GitHub REST (every job).**
   `gather_context` is `async` (`src/v2/pipeline/stages/context_gather.py:585`) but
   calls fully-synchronous provider methods inline:
   - `providers.github.get_repository(...)` (`context_gather.py:603`)
   - `providers.github.get_contributors(...)` (`:612`)
   - `providers.github.get_languages(...)` (`:627`)
   - `providers.github.get_repository_readme(...)` (`:637`)
   - `providers.github.get_repository_jsonld(...)` (`:655`) → the **gimie sidecar**
     call, with a **180s** default timeout.
   - `providers.github.get_repository_aux_files(...)` (`:658`), releases (`:668`), etc.
   The `GitHubProvider` has **zero `async def` methods** (grep: 0 hits) — every
   method is blocking `requests.get(...)` (e.g. `github_provider.py:778, 904, 943,
   993, 1024, 1054, 1108, 1169, 1271, 1341, 1395, 1504, 1542, 1603, 1720`).
   None of these calls in `gather_context` are wrapped in `to_thread`
   (grep for `to_thread`/`run_in_executor` in `context_gather.py` and
   `orchestrator.py`: **0 hits**).
2. **Synchronous `time.sleep` inside the loop.**
   `src/v2/ingest/providers/github_provider.py:578` — the gimie-payload fetch
   retries JSON-decode failures with `time.sleep(backoff_seconds)` (`2**attempt *
   0.5`s). Because the call chain is `await gather_context → (sync)
   get_repository_jsonld → _get_or_fetch_gimie_payload`, this `time.sleep` blocks
   the **whole event loop**, not just the one job.
3. **The gimie sidecar HTTP client is sync.**
   `src/v2/ingest/providers/gimie_api_client.py:69` `http.get(url,
   timeout=_timeout())` with `_DEFAULT_TIMEOUT_SECONDS = 180.0`
   (`gimie_api_client.py:28`). gimie clones + parses the repo, so this single
   blocking call routinely takes tens of seconds.
4. **Hybrid-only refiner blocking calls.**
   - `src/v2/pipeline/stages/refine_with_llm.py:555` `requests.get(url,
     params=params, timeout=15)` (`_openalex_lookup_doi`, `:532`) is reached
     from the async `_run_discovery_pass` (`:1975`) via the sync
     `_materialize_article` (`:856`, called inline at `:2131`) — **not** wrapped
     in `to_thread`, even though sibling DuckDB calls in the same file *are*
     (`refine_with_llm.py:1248, 1268, 1281, 1315`). So hybrid adds *another* class
     of loop-blocking network I/O on top of context-gather.
   The actual LLM calls themselves are fine — `V2LLMRuntime.run_json_prompt`
   does `await agent.run(...)` (`src/v2/agents/llm/runtime.py:350`, pydantic-ai
   async), and per-stage fan-out uses `asyncio.Semaphore`
   (`orchestrator.py:628`) + `asyncio.gather` (`:740`). The problem is the
   *synchronous provider I/O*, which hybrid exercises more of and for longer.

### Polling — `GET /v2/jobs/{job_id}` and `GET /v2/crawl/{job_id}`
- `src/v2/api.py:2406` `extract_job(...)` and `src/v2/api.py:2466` `crawl_status(...)`
  both call `_resolve_extract_record` (`:2369`) → `JobStore.get` (`src/v2/jobs.py:26`),
  which reads the record from the SQLite-backed `ProviderCache`. This read is
  itself synchronous (`self._cache.get(...)`, `jobs.py:27`) and runs on the loop.
- Liveness: `_maybe_mark_extract_job_stale` (`src/v2/api.py:2327`) flips a `RUNNING`
  job to `FAILED` only after **600s** without a heartbeat
  (`_JOB_STALE_THRESHOLD_SECONDS = 600.0`, `src/v2/api.py:261`). But the heartbeat
  writer (`_heartbeat`, `src/v2/api.py:540`) is *also a coroutine on the same loop*
  — when the loop is blocked by a sync gimie/GitHub call, the heartbeat `await
  asyncio.sleep(30)` cannot fire, so heartbeats stall exactly when the worker is
  busiest. (This usually self-corrects once the blocking call returns, but it
  widens the window for false "stale" flips and for poll timeouts.)

### Server worker model
- Dev/default serve: `uvicorn src.api:app --workers {{WORKERS}}` with
  `WORKERS := env_var_or_default("WORKERS", "4")` (`justfile:10, 41`).
- Production compose: `gunicorn ... --worker-class uvicorn.workers.UvicornWorker`
  (`justfile:59`) with `WORKERS: ${WORKERS:-2}` in
  `tools/deploy/docker-compose.yml:33`.
- Each uvicorn worker is a **single event loop in a single process**. So with
  `WORKERS=2` the server can truly run only 2 jobs at a time *if* each job yields
  the loop — but because jobs block the loop (above), each worker is effectively
  pinned to **one** in-flight extract until it finishes. There is no per-request
  thread offload, so a worker serving a blocked extract **cannot even answer the
  poll request** for that job (or any other job routed to it) until the blocking
  call returns.

---

## Likely root cause(s)

Ranked most→least likely:

1. **Blocking sync I/O on the event loop starves the HTTP server (primary).**
   `gather_context` and the hybrid refiner call synchronous `requests.get` (gimie
   sidecar up to 180s; GitHub REST; OpenAlex) and a synchronous `time.sleep`
   (`github_provider.py:578`) directly on the loop, with no `to_thread`. While one
   extract job is inside such a call, the worker's event loop is frozen: it cannot
   accept new connections, complete the TLS/HTTP handshake, or send responses to
   in-flight polls. To the client that surfaces as connection resets / "server
   disconnected" and as **no throughput gain** — extra concurrent jobs just queue
   behind the frozen loop. Hybrid is hit hardest because (a) it runs the extra
   `refine_with_llm` stage with its own blocking OpenAlex call, and (b) its longer
   wall time keeps the loop blocked longer per job.

2. **Worker count too low to absorb concurrency, with no offload to compensate.**
   `WORKERS=2` (prod) / `4` (dev) means only 2–4 loops exist; combined with (1),
   the *effective* concurrency is ~`WORKERS` blocking jobs. Client `max_workers`
   above that gets connection failures rather than queueing politely. There is no
   `--timeout-keep-alive` / `--limit-concurrency` tuning to make the overload
   graceful.

3. **Heartbeat + poll both live on the blocked loop.**
   The 30s heartbeat (`api.py:540`) and the poll reads (`api.py:2406/2466`) are
   coroutines on the same starved loop, so they can't make progress during a
   blocking stage. This compounds (1): the client's poll *and* the liveness signal
   stall together, so the client can't even get a clean "still running" answer.

4. **(Contributing, not primary) gunicorn/uvicorn default keep-alive + no graceful
   backpressure.** With the loop frozen past the keep-alive window, the server may
   drop idle keep-alive connections, which the client sees as resets on its *next*
   poll on that connection.

---

## Proposed fix — short-term (docs / config)

Goal: give operators a *real* safe-concurrency number and make overload less
violent, without touching pipeline code.

1. **Document the real safe concurrency for hybrid.** In
   `docs/OPERATIONS_RUNBOOK.md` (referenced by the deploy compose header) and in
   the compose file comments, state plainly:
   - With the current synchronous-provider pipeline, **effective hybrid
     concurrency ≈ server `WORKERS`**. Client-side `max_workers` should be set to
     **`WORKERS` (or `WORKERS - 1`)**, not higher. Going above that yields
     connection resets, not throughput.
   - Recommend `WORKERS = min(CPU_cores, desired_parallel_hybrid_jobs)` and size
     the box accordingly; each worker holds one blocking job.
2. **Raise `WORKERS` deliberately** (`tools/deploy/docker-compose.yml:33`,
   `WORKERS: ${WORKERS:-2}`). Because work is blocking-but-mostly-I/O-bound, you
   can over-subscribe CPU somewhat (e.g. `WORKERS = 2–4 × cores`) to get real
   hybrid parallelism *today* — at the cost of memory (each worker re-loads the
   app + provider caches) and more concurrent gimie-sidecar / LLM load. This is the
   cheapest lever that actually increases throughput before the structural fix.
3. **Tune uvicorn/gunicorn for graceful overload** in the serve commands
   (`justfile:41, 59`):
   - `--timeout-keep-alive` (uvicorn) high enough that a busy worker doesn't drop
     idle poll connections.
   - gunicorn `--timeout` must exceed the longest extract (gimie 180s + LLM stages
     → set e.g. `--timeout 1200`) so gunicorn doesn't kill a worker mid-extract
     (which itself would cause resets and orphaned `RUNNING` jobs).
   - Consider `--limit-concurrency` (uvicorn) so excess load returns `503` (which
     the client can back off on) instead of a reset.
4. **Tell clients to poll `GET /v2/crawl/{job_id}`** (the lightweight status,
   `api.py:2466`) rather than the full-graph `GET /v2/jobs/{job_id}` while waiting,
   and to use a generous poll interval + retry-with-backoff on connection errors
   (treat reset as "still running", not "failed"). Document this in the runbook.

Trade-offs: (1)–(4) are config/docs only and ship today, but they cap throughput
at `WORKERS` and waste memory if `WORKERS` is pushed high. They make the failure
mode *graceful* (503/queue) rather than *eliminating* it.

## Proposed fix — structural

Goal: stop blocking the event loop so a single uvicorn worker can actually serve
many concurrent hybrid jobs (and keep answering polls while they run).

1. **Offload all synchronous provider I/O to a thread pool (highest leverage).**
   Wrap the blocking provider calls reached from async stages in
   `asyncio.to_thread(...)` (or push the wrapping down into the provider). Concrete
   targets:
   - `src/v2/pipeline/stages/context_gather.py:603, 612, 627, 637, 655, 658, 668`
     — wrap each `providers.github.*` call. (The cleanest version: give
     `GitHubProvider` thin `async` wrappers that `to_thread` the sync body, so
     callers just `await`.)
   - `src/v2/pipeline/stages/refine_with_llm.py:2131` `_materialize_article` (and
     thus `_openalex_lookup_doi`, `refine_with_llm.py:532/555`) — wrap in
     `to_thread`, matching the existing pattern already used in the same file at
     `refine_with_llm.py:1248, 1268, 1281, 1315`.
   - Replace the in-loop `time.sleep` at `src/v2/ingest/providers/github_provider.py:578`
     with either a non-blocking `await asyncio.sleep` (if the function becomes
     async) or keep it but ensure the whole function runs inside `to_thread`.
   Bound the thread pool (e.g. raise the default executor `max_workers`, or use a
   dedicated `ThreadPoolExecutor` sized for the gimie/GitHub fan-out) so it doesn't
   create unbounded threads under load. This single change is what restores
   throughput-with-concurrency for hybrid.
   - Trade-off: many small edits; must audit *every* sync provider call on an async
     path (grep `providers.github.` / `requests.get` under `src/v2/pipeline`); risk
     of missing one and still blocking. Relates to Bug #1 — see below: don't move
     DuckDB writes into threads without honouring the existing serialization lock.

2. **Cap in-flight extract jobs explicitly + return backpressure.**
   Add an `asyncio.Semaphore` (or a small bounded queue) around the body of
   `_run_extract_job` (`src/v2/api.py:506`) sized by a new
   `V2_MAX_CONCURRENT_EXTRACT_JOBS` env var. When at capacity, `POST /v2/extract`
   either queues (job stays `PENDING`, which the client already handles) or returns
   `503`. This makes overload deterministic instead of a connection-reset lottery,
   and pairs naturally with (1) (threads are finite).
   - Trade-off: introduces a queueing concept the current code lacks; need to make
     sure `PENDING` jobs don't count against the stale-heartbeat timer.

3. **Decouple the job runner from the request loop (larger).**
   Today the "background task" is a task on the HTTP loop (`api.py:2301`). Move
   execution to a separate executor/process so HTTP serving and extraction don't
   share a loop:
   - Lighter: run `_run_extract_job` bodies in a dedicated worker thread/process
     pool from the start (so the request loop only ever does fast JobStore reads
     for polls).
   - Heavier: a real job queue + worker model (e.g. an in-process worker pool with
     a `JobStore`-backed queue, or an external broker). The `JobStore`
     (`src/v2/jobs.py`) + heartbeat + stale-detection scaffolding already model a
     decoupled queue — this would make it real and would survive worker restarts.
   - Trade-off: biggest change; only worth it if (1)+(2) don't suffice. With (1),
     a single loop should already handle many concurrent hybrid jobs because the
     blocking work is in threads and the loop stays responsive to polls.

4. **Tune keep-alive/timeouts as a permanent part of the deploy** (fold the
   short-term config into the committed serve commands / compose, not just docs),
   so a slow gimie call never trips a worker timeout or drops a poll connection.

Recommended sequencing: **(1) first** (it is the actual fix for both symptoms —
resets *and* no-throughput-gain), then **(2)** for graceful backpressure, then
**(4)**; defer **(3)** unless profiling after (1)+(2) still shows the loop
saturated.

---

## Risks & considerations

- **Thread-safety of providers.** Moving sync provider calls into `to_thread`
  means concurrent threads may touch the shared `ProviderCache` (SQLite) and the
  in-memory `_gimie_payload_cache` dict (`github_provider.py:524`). SQLite is in
  WAL mode (pre-warmed at startup, `src/api.py:120-131`) but concurrent writes
  still need care — **this is the seam with Bug #1**; coordinate so the DuckDB/
  SQLite write paths keep their serialization (there are already module-level
  `threading.Lock`s, e.g. `src/v2/api.py:1806-1809`). Don't naively parallelize a
  path that Bug #1 deliberately serializes.
- **Thread-pool sizing.** Unbounded `to_thread` under high client concurrency can
  spawn many threads each holding a 180s gimie call. Pair (1) with the job-level
  semaphore (2) and a bounded executor.
- **gunicorn `--timeout`** must be raised *before* offloading helps in prod,
  otherwise a long gimie call still trips the worker kill.
- **Per-worker memory.** Raising `WORKERS` (short-term) multiplies app + cache
  memory; verify the deploy box has headroom.
- **Heartbeat semantics.** Once the loop stops being blocked (after fix 1),
  heartbeats fire reliably; the 600s stale threshold (`api.py:261`) can likely be
  lowered, but only *after* the loop is non-blocking — don't lower it first.

## Test / verification plan

1. **Repro harness.** Script that submits N concurrent `POST /v2/extract`
   `runtime=hybrid` jobs against a real repo and polls each via
   `GET /v2/crawl/{id}`; record per-poll latency, connection errors, and total
   wall-clock for the batch. Confirm at `WORKERS=2, N=8` we see the resets / flat
   throughput described.
2. **Event-loop blocking probe.** Add a temporary lightweight `/healthz` that just
   returns `{"ok": true}` and hit it on a tight interval *during* a hybrid extract.
   Pre-fix: `/healthz` latency spikes to seconds (loop blocked) during
   context-gather/refiner. Post-fix (to_thread): `/healthz` stays sub-10ms. This
   directly proves/disproves root cause #1.
3. **Unit-ish:** assert that `gather_context` and `_run_discovery_pass` issue their
   blocking provider calls via `to_thread` (e.g. patch `asyncio.to_thread` and
   assert it's invoked; or assert the provider's sync method isn't called on the
   running loop thread).
4. **After fix:** re-run (1) and confirm (a) zero connection resets, (b) batch
   wall-clock scales down with concurrency up to the new semaphore cap, (c) polls
   stay responsive throughout.
5. **Regression:** run the existing v2 pipeline tests + a single-job hybrid extract
   to confirm output is byte-identical (offloading must not change results).

## Effort estimate

- Short-term (docs + WORKERS + uvicorn/gunicorn timeout/keep-alive flags):
  **~0.5–1 day**, no code risk.
- Structural fix (1) `to_thread` offload of provider calls + bounded executor:
  **~2–4 days** including the audit of every sync call on an async path and the
  Bug #1 coordination.
- Structural fix (2) job-level semaphore/backpressure: **~1 day**.
- Structural fix (3) decoupled runner/queue (only if needed): **~1–2 weeks**.
- Repro + verification harness: **~1 day** (worth doing first to confirm the
  hypothesis before investing in the offload).

## Open questions

- **Has the loop-block been confirmed under instrumentation?** The mechanism is
  code-evident, but the `/healthz`-during-extract probe (test step 2) hasn't been
  run — do that first; it's cheap and decisive.
- **Which provider calls are *actually* on the hot path for the failing config?**
  If the gimie sidecar is the dominant blocker, offloading just `get_repository_jsonld`
  may capture most of the win; profile to prioritize.
- **Is the gimie sidecar itself a bottleneck/serialization point?** Even with
  client→server offload, if the single `gme-gimie-api` container serializes
  requests, concurrency is capped there instead (overlaps with Bug #4 load
  isolation). Check the sidecar's own worker model.
- **Target deployment `WORKERS` and box size?** The short-term recommendation
  needs the operator's CPU/memory budget to give a concrete number.
- **Should the runner move off the request loop entirely (fix 3)?** Decide after
  measuring fix (1)+(2); only pursue if a single loop still saturates.

---

### Relationship to Bug #1 and Bug #4 (no overlap intended)

- **Bug #1 (DuckDB write contention):** separate failure — that one is about
  concurrent DuckDB writers / `database is locked`, serialized today by the
  module-level `threading.Lock`s (`src/v2/api.py:1806-1809`) and the WAL pre-warm
  (`src/api.py:120-131`). This plan must **not** undo that serialization: when
  fix (1) moves sync work into threads, the DuckDB write paths must keep their
  locks. Bug #1 is "writes collide"; Bug #02 is "the event loop is frozen by sync
  I/O" — different layers, shared seam at the thread-pool/lock boundary.
- **Bug #4 (load isolation):** about isolating heavy load so it can't take down
  the rest of the service. This plan's job-level semaphore (structural fix 2) and
  keep-alive/timeout tuning are complementary inputs to Bug #4 but don't define its
  isolation strategy; the gimie-sidecar serialization question above is the most
  likely overlap. Defer the isolation design itself to Bug #4's plan.
