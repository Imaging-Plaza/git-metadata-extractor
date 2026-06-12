# Bug 03 — Memory growth / per-extraction leak in extraction service
**Severity:** medium-high (forces restarts) · **Status:** Investigated — plan ready (no code changed) · **Area:** lifecycle / caching / connections

## Symptom
Over ~4,700 sequential extractions, `git-metadata-extractor` RSS climbed from ~0.5 GiB to ~5.1 GiB / 6 GiB and throughput degraded roughly linearly (1.2 s → ~6.7 s per entity). A process restart cleared RSS back to ~0.5 GiB and restored throughput. This is the signature of a **per-extraction leak** (objects retained on the process / module globals across requests, plus connection/client objects that are created per request and never closed). The slowdown that tracks RSS growth points at one or more **per-process containers that are scanned/iterated and grow without bound** (each extraction does more work as the container grows), compounded by GC pressure and connection-pool churn.

Important framing: **this is hypothesis-driven**. No profiler run has confirmed which site dominates. The ranking below is by structural likelihood (unbounded growth + lives across requests + on the hot extraction path), but the Diagnosis plan must run *before* any fix to attribute the actual bytes.

## Ranked suspect leak sites (file:line + rationale)

### S1 — Per-agent-run model/provider/HTTP-client creation, never closed (TOP SUSPECT)
- **`src/v2/agents/llm/runtime.py:313`** — `model = create_pydantic_ai_model(config)` is called **inside `run()`**, i.e. once per agent invocation, immediately followed by `agent = Agent(model=...)` at `runtime.py:316`.
- **`src/v1/llm/model_config.py:556`** (`create_pydantic_ai_model`) — for the non-default providers it constructs a **fresh provider object each call**: `OpenAIProvider(base_url=..., api_key=...)` (model_config.py:585-589), `OllamaProvider(base_url=...)` (model_config.py:597-600), `OpenRouterProvider(...)` (model_config.py:578). Each pydantic-ai provider wraps a new `AsyncOpenAI`/`httpx.AsyncClient` with its own connection pool. None are ever `.close()`/`.aclose()`'d — there is **no teardown in the LLM path** (`grep` for `aclose`/`close`/`gc.collect` in `src/v2/agents/llm/runtime.py` and `src/v2/pipeline/` returns nothing for the LLM clients; only DuckDB cons are closed).
- **Why it leaks across extractions:** a single extraction fans out across many LLM agents — repository, person, organization, article, membership, contribution, context-summary, critic, plus refiners (`refine_with_llm.py` imports `RepositoryRefinerAgent`, `PersonRefinerAgent`, `OrganizationRefinerAgent`, `MembershipRefinerAgent`, `DiscoveryRefinerAgent`, `OrgResolverAgent`, `RescueRefinerAgent`, `RorParentSelectorAgent` at `src/v2/api.py:1464`). Each agent `run()` mints a new client. httpx clients with live pools/transports are not promptly GC'd (background connections, `__del__` semantics, event-loop references), so unclosed `AsyncOpenAI`/`httpx` clients accumulate. At dozens of clients per extraction × thousands of extractions this dominates both RSS and the GC scan cost (more live objects → slower collections → throughput decay).
- Note: the default `provider == "openai"` branch (`OpenAIChatModel(model_name)` at model_config.py:574) defers client creation to pydantic-ai internals; the leak surface is largest for `openai-compatible`/`ollama`/`openrouter`, which is the likely production config (self-hosted/Ollama).

### S2 — Unbounded module-level `_search_cache` in Infoscience client (HIGH)
- **`src/v2/ingest/infoscience.py:32`** — `_search_cache: Dict[str, str] = {}` is a **module-global dict** populated at infoscience.py:734, :784, :831, :881 and read at :720, :772, :819, :861.
- The only clearer, **`clear_infoscience_cache()` (src/v2/ingest/infoscience.py:35) is never called anywhere in v2** — `grep` for `clear_infoscience_cache` shows callers only in v1 (`src/v1/agents/linked_entities_enrichment.py:740/835/900`) against the *v1* module `src/v1/context/infoscience.py`. The v2 cache therefore grows for the entire process lifetime, one entry per unique person/lab/publication search across **all** extractions.
- **Why it leaks + slows:** keys are search strings (unbounded cardinality); values are full markdown result blobs (can be large). It both grows RSS and, because it is consulted on every Infoscience tool call, contributes to the linear "does more each time" slowdown. The v2 module is on the extraction path: `src/v2/ingest/providers/infoscience_provider.py` imports `search_authors`/`search_labs` from `src.v2.ingest.infoscience` (infoscience_provider.py:122,:130).

### S3 — Per-request provider `requests.Session` objects, lazily created and never closed (MEDIUM-HIGH)
- **`src/v2/dependencies.py:434-439` / `_default_provider_set` (dependencies.py:328) / `get_provider_set` (dependencies.py:413)** — the FastAPI dependency builds a **fresh `ProviderSet` on every `/extract` request** (`RealGitHubProvider`, `RealORCIDProvider`, `RealInfoscienceProvider`, `RealRORProvider`, `PackageRegistryProvider`).
- **`src/v2/ingest/providers/orcid_provider.py:175-178`** and **`src/v2/ingest/providers/ror_provider.py:192-195`** — `_http_client()` lazily does `self._session = requests.Session()` and the session is **never closed** (`grep` for `close` in those files → none). Each request that hits ORCID/ROR creates a `requests.Session` (urllib3 connection pool, adapters) that is dropped without `.close()`.
- **`src/v2/dependencies.py:405`** — `_optional_orcid_oauth_session()` builds another `requests.Session()` per request when OAuth creds are present; also not closed.
- **Why it leaks:** unclosed `requests.Session` retains pooled sockets + adapter state. Per request × thousands of requests, these accumulate until GC, and GC of socket-holding objects is not immediate. Lower-ranked than S1/S2 only because the per-instance footprint is smaller and these are sync (urllib3) pools rather than async transports.

### S4 — `_gimie_payload_cache` per provider instance (LOW, but worth confirming)
- **`src/v2/ingest/providers/github_provider.py:487`** — `self._gimie_payload_cache: dict[str, Any] = {}` holds full GIMIE JSON-LD payloads keyed by repository URL (populated at github_provider.py:531 etc.).
- Because the provider is rebuilt per request (S3), this dict is **bounded within one extraction** and dropped with the provider — *unless* a provider override is stashed on `app.state` (`v2_github_provider`, resolved at dependencies.py:466). If a long-lived provider instance is ever placed on app state, this dict becomes an unbounded cross-request cache. Verify the deployment does not pin a provider on app.state.

### S5 — Bare `requests.get()` per gimie sidecar call (LOW — pooling, not a leak)
- **`src/v1/gimie_utils/gimie_methods.py:159`** — `requests.get(...)` with no shared session: opens/closes a connection per call. Not a memory leak, but contributes to socket churn against the `gme-gimie-api` sidecar and is worth a shared-session cleanup while we are in here. Listed for completeness.

### Ruled out (checked, not leaking)
- **`ProviderCache` (`src/v2/ingest/cache.py`)** — every method opens a `sqlite3` connection inside a `with` block and closes it (cache.py:79, `get`/`set`/`get_or_set`/`clear`). SQLite-backed (`api_cache.db` / `.cache/v2/providers.db`), TTL-bounded, no in-RAM growth. The root `api_cache.db` is a persisted artifact, not an in-memory structure.
- **Selenium fetch (`src/v2/agents/llm/agent_tools/selenium_fetch.py:33`)** — `_load_html_via_selenium` uses `try/finally: driver.quit()` (selenium_fetch.py:65); the remote WebDriver session is torn down each call. Not leaking.
- **`QueryLog` (`src/v2/observation/query_log.py:42`)** — instantiated per extraction (`run_id`), held in a `ContextVar` (`query_log_var`, query_log.py:120) that dies with the request task. Per-request, not cross-request.
- **DuckDB in pipeline stages** — `rule_based_disciplines.py:200`→closed at :220; `refine_with_llm.py:1106/1154`→closed in `finally` (:1133, :1178). Stats/index paths cache a read-only handle on the store object, not per extraction.
- **`@lru_cache` sites** — all bounded: `agents/models.py:228` (maxsize=8), `ingest/detection/github_url_classifier.py:112` (maxsize=512), `schema/__init__.py:13` and `validation/ontology.py:54` (maxsize=1), `validation/schema_validation.py:57` (maxsize=8). Keys are low-cardinality; not a leak.
- **`model_config._API_KEY_CYCLES` / `_API_KEY_SOURCES` (model_config.py:18-19)** — keyed by env-var name (one entry per key env var), bounded.

## Diagnosis plan (confirm before fixing)
Goal: attribute the growing bytes to a specific site before changing code. Run against a staging instance driven through a representative batch (a few hundred extractions is enough to see the trend; the slope is linear).

1. **RSS-per-N logging (cheapest signal).** Add a lightweight middleware/loop that logs `psutil.Process().rss` (or `resource.getrusage`) every N extractions along with `len(gc.get_objects())`, `gc.get_count()`, and `gc.collect()` return. Confirm the slope and whether a forced `gc.collect()` recovers memory (if it does → reference-cycle / delayed-finalizer problem, points at S1 httpx clients; if it does not → live references, points at S2 module dict).
2. **`tracemalloc` snapshots between extractions.** `tracemalloc.start(25)`; snapshot after extraction #10 and #200; `snapshot2.compare_to(snapshot1, "lineno")` and print top 30. This will name the exact allocating file:line — expect `infoscience.py` (S2), pydantic-ai/openai/httpx internals (S1), or urllib3 pools (S3) to top the diff.
3. **`objgraph` type-count growth.** Between extractions, `objgraph.show_growth(limit=30)`; then `objgraph.show_backrefs` / `objgraph.count('Session')`, `count('AsyncOpenAI')`, `count('AsyncClient')`, `count('Connection')`. Rising counts of `AsyncClient`/`AsyncOpenAI` confirm S1; rising `Session`/`HTTPConnectionPool` confirm S3; rising dict size in `infoscience` confirms S2.
4. **Targeted asserts for S2.** Log `len(src.v2.ingest.infoscience._search_cache)` every N extractions — a monotonic climb is direct proof and trivially correlates with the throughput decay (dict scanned on each lookup).
5. **GC tunables / leak hunt.** Temporarily set `gc.set_debug(gc.DEBUG_SAVEALL)` on a short run to enumerate uncollectable cycles; check `gc.garbage`. httpx/openai clients with `__del__` or live transports commonly land here.
6. **A/B isolation.** Re-run the batch with `V2_USE_MOCK_PROVIDERS=true` (no real sessions/HTTP) and with the LLM stages disabled in turn, to see which subsystem's removal flattens the RSS slope. This cheaply separates S1 (LLM) from S3 (providers) from S2 (infoscience).

Capture before/after RSS slope (MiB per 100 extractions) as the headline metric for every fix.

## Proposed targeted fixes
Apply in priority order; gate behind the diagnosis attribution.

- **S1 — reuse model clients instead of minting one per agent run.**
  - Cache the constructed pydantic-ai `Model`/provider per `(provider, model, base_url, api_key_env)` config signature (e.g. an `@lru_cache`/dict keyed on a frozen config tuple in `src/v1/llm/model_config.py:556`, or memoize at `src/v2/agents/llm/runtime.py:313`). The underlying `AsyncOpenAI`/`httpx.AsyncClient` is designed to be long-lived and pooled; one per config for the whole process is correct and also faster (no pool warm-up per agent). This both stops the leak and directly helps throughput.
  - If full reuse is risky (per-request key rotation via `_next_api_key`), at minimum ensure the client is **closed** at the end of each agent run: wrap the run so `await model_client.aclose()` (or the provider's close) executes in a `finally`. Confirm the exact close hook pydantic-ai exposes for the version in `uv.lock` before wiring.
- **S2 — bound / scope the Infoscience cache.**
  - Simplest correct fix: clear `src/v2/ingest/infoscience.py:_search_cache` at the end of each extraction (call the existing `clear_infoscience_cache()` from the orchestrator/`/extract` teardown), matching the v1 pattern that already does this in `linked_entities_enrichment.py`.
  - Better: replace the raw module dict with a bounded structure (`functools.lru_cache(maxsize=...)` on the underlying fetch, or a TTL/`maxsize` cache), so it stays a within-process speedup without unbounded growth. Or move it into the request-scoped `ProviderCache` (already SQLite + TTL bounded) to match how every other provider caches.
- **S3 — close provider sessions; or reuse a shared session.**
  - Give `RealORCIDProvider`/`RealRORProvider` (and the OAuth session at `dependencies.py:405`) a `.close()` and call it when the per-request `ProviderSet` is done (FastAPI dependency teardown / `finally` around the orchestrator run in `api.py:444`). Cleanest: make `ProviderSet` a context manager that closes all owned sessions.
  - Alternative: construct one shared `requests.Session` per process (stored on `app.state`) and inject it (the providers already accept a `session=` arg, orcid_provider.py:163 / ror_provider.py:180), eliminating per-request session churn entirely.
- **S5 (bonus) — share a session for the gimie sidecar** in `gimie_methods.py:159` to cut socket churn against `gme-gimie-api`.
- **Job-end hygiene (defensive, all suspects).** At the end of each extraction add explicit `del` of large per-request structures and a periodic `gc.collect()` (e.g. every N extractions, not every one — collecting each time is itself a throughput tax). This is a safety net, not a substitute for closing clients.

## Operational stopgap (worker recycling)
The leak is real **today** and big batches already need manual restarts — ship a recycle stopgap immediately, independent of the code fixes:

- **Gunicorn/uvicorn worker recycling:** run under gunicorn with `--max-requests N --max-requests-jitter J` (e.g. `--max-requests 200 --max-requests-jitter 40`) so each worker is recycled after ~N extractions, reclaiming all leaked RSS automatically. Jitter staggers restarts so workers don't all recycle at once. This is the recommended primary stopgap — zero code change, drop-in for the deploy compose (`feat/deploy-compose`).
- **If not behind gunicorn:** add an in-process self-restart after N extractions (counter on app.state; when it crosses the threshold and no request is in flight, exit so the container's `restart: unless-stopped`/orchestrator brings it back). Pick N from the observed slope so peak RSS stays comfortably under the 6 GiB cap (e.g. ~0.5 GiB + slope×N ≤ ~3-4 GiB).
- **Memory guardrail:** set a container memory limit + restart policy in the production compose so an OOM is a clean recycle rather than a hard failure. Tune N below that limit.

Keep the stopgap even after the code fixes land, as defence-in-depth, until a long-batch RSS-flat run is demonstrated.

## Risks & considerations
- **Client reuse (S1) and event loops:** an `AsyncOpenAI`/`httpx.AsyncClient` is bound to the event loop it was created on. Under a single uvicorn worker loop this is fine; under multiple loops/threads a per-loop or per-config cache is needed. Validate against the actual pydantic-ai version in `uv.lock`.
- **API-key rotation:** `create_pydantic_ai_model` calls `_next_api_key()` (model_config.py) which rotates a key pool per call. Caching the client by config must account for rotation intent — key the cache by env-var name (not the rotated value), or rotate at the request layer, so reuse doesn't pin one key.
- **Clearing `_search_cache` between extractions** removes the cross-extraction dedupe benefit; acceptable since the persistent `ProviderCache` already covers cross-request reuse with bounds. Prefer the bounded-cache variant if intra-batch dedupe matters.
- **Closing shared sessions** must happen exactly once and after all in-flight use; closing a session another concurrent request still holds would break it. The context-manager-per-request approach avoids this.
- **`gc.collect()` cost:** forcing collection every extraction can *worsen* throughput; gate it to every N.
- **Worker recycling drops warm caches** (in-memory `_search_cache`, lru_caches, warmed pools) on each recycle, a small latency blip on the first post-recycle request — acceptable and tunable via N.

## Test / verification plan
1. **Reproduce + baseline:** run a fixed batch (e.g. 300 identical/representative extractions) on staging with the diagnosis instrumentation; record RSS slope (MiB/100), per-entity latency trend, and `objgraph` type-count growth. This is the regression baseline.
2. **Per-fix A/B:** apply one fix at a time; re-run the same batch; confirm the targeted counter flattens — `len(_search_cache)` bounded (S2), `objgraph.count('AsyncClient'|'AsyncOpenAI')` flat (S1), `objgraph.count('Session')` flat (S3) — and that overall RSS slope drops toward ~0.
3. **Throughput correlation:** confirm per-entity latency no longer climbs with extraction count once the dominant suspect is fixed.
4. **Soak test:** run ≥4,700 extractions (matching the reported failure scale) and assert peak RSS stays well under 6 GiB and final RSS ≈ initial RSS within noise.
5. **Stopgap validation:** with gunicorn `--max-requests` set, confirm workers recycle on schedule, RSS sawtooths back to baseline, and no requests are dropped during recycle (jitter + graceful shutdown).
6. **Functional regression:** existing v2 extraction tests (`tests/v2/`) pass; spot-check that client reuse / session close didn't change extraction output (same JSON-LD for a fixed fixture repo) and that ORCID/ROR/Infoscience lookups still succeed after a session is reused/closed.

## Effort estimate
- Diagnosis instrumentation + attribution run: **0.5–1 day**.
- Operational stopgap (gunicorn `--max-requests` in deploy compose): **~1 hour** (ship first).
- S2 fix (bound/clear infoscience cache): **~0.5 day** incl. test.
- S3 fix (close/reuse provider sessions via `ProviderSet` lifecycle): **0.5–1 day**.
- S1 fix (model-client reuse/close, version-validated against pydantic-ai): **1–2 days** (highest-value, highest-care).
- Soak + verification: **0.5–1 day**.
- **Total: ~3–5 days** including verification; stopgap mitigates the operational pain on day one.

## Open questions
- Which provider is configured in production (`openai` vs `openai-compatible`/`ollama`/`openrouter`)? This determines how much of S1 actually fires (the default `openai` branch defers client creation).
- Confirm with a profiler which of S1/S2/S3 dominates — the ranking is structural, not measured.
- Is the service already behind gunicorn, or run as a single uvicorn process? Determines whether the stopgap is config-only or needs the self-restart shim.
- Does the deployment ever pin a provider/orchestrator on `app.state` (which would promote S4 from per-request to cross-request)?
- What pydantic-ai / openai / httpx versions are pinned in `uv.lock`, and what is the supported close/reuse API for their clients?
- Acceptable peak-RSS ceiling and target batch size — sets the worker-recycle N.
