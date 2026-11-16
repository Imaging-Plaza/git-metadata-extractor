# Sources concurrent-read performance

Request: improve performance and handle simultaneous requests through the sources
HTTP/MCP service. Continue the existing local refactor branches.

## Design and scope

Keep one service process, preserving DuckDB writer coordination and all tool/API
contracts. Existing async dispatch already allows parallel calls, but synchronous
Qdrant existence checks block every other coroutine, and common RCP clients open
fresh connection pools per call. Fix these measured paths rather than increasing
workers (unsafe with process-local writers) or replacing all adapters/storage.

Move Qdrant preflight I/O to worker threads. Let the service opt its cached common
RCP embedding/reranking clients into persistent, bounded HTTPX connection pools,
closing them through service shutdown. Standalone/index CLI uses retain scoped
clients, including callers using separate asyncio.run loops. Preserve auth,
timeouts, payloads and upstream credentials. No result cache or stale-index cache.

This is an implementation choice within the requested local optimization. It
does not claim unlimited throughput or change upstream quotas, writer locking,
federated fan-out, deployment topology or public request schemas.

## Implementation plan

Use subagent-driven development for the independent client-pooling task and
review; root implements Qdrant offloading and transport benchmarks.

- [x] Add failing deterministic tests that assert preflight checks run outside
  the event-loop thread across every affected provider; offload those checks.
- [x] Add pooled-client lifecycle, concurrent reuse, failure cleanup and CLI
  compatibility tests; implement service-owned common RCP pooling.
- [x] Measure identical fake-backend HTTP/MCP workloads at concurrency 1/4/8/16
  before and after; test responsiveness and concurrent result isolation.
- [x] Run non-live sources suite, paired contracts, Docker smoke, lint and docs;
  document measurements and limits, commit sources and update GME's lock.

## Review focus

No cross-event-loop HTTP clients; no extra pools under simultaneous first calls;
shutdown closes clients once; failed/cancelled calls do not poison other callers;
DuckDB writes remain coordinated; benchmarks are synthetic and labeled as such.

## Verification and outcome

- 902 sources non-live tests passed, network disabled and tokenizer cache mounted.
- 3 paired HTTP/MCP contracts passed; Docker mixed 8 MCP + 4 HTTP lookups + health
  passed, plus the existing extractor/run-report smoke checks.
- Frozen-lock sources image built as 0.2.0.dev2; uv lock offline check passed.
- Strict MkDocs build and import closure passed. Ruff found no new diagnostics;
  the nine one-line provider changes retain 74 pre-existing lint diagnostics.
- Synthetic dispatch benchmark at concurrency16: HTTP18.80 ->130.25 req/s,
  MCP17.63 ->95.75 req/s. Fake 50ms preflight plus50ms search,32requests per row,
  warm in-process transports. No claim about actual model/Qdrant capacity or
  pooling speedup. Full data and reproduction live in sources docs/performance.md.
- Independent review found and fixed Python3.10 test compatibility and a
  CPU-dependent barrier test; final targeted review rerun24passed with two cores.
- Keep one worker; cold provider initialization, service-wide admission control,
  federated fan-out budgets and ingest/read isolation remain future measurements.
- Preserve local feature branches; independent sources commit then parent pin.
