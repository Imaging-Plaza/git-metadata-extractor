# Bug 01 — rule_based_disciplines DuckDB access-mode conflict (workers>1)
**Severity:** medium · **Status:** Investigated — plan ready (no code changed) · **Area:** concurrency / epfl_graph index / disciplines stage

## Symptom
Per-entity WARNING, emitted roughly once per concurrent worker, only when extraction
runs with more than one worker:

```
rule_based_disciplines: duckdb open failed — Connection Error: Can't open a
connection to same database file with a different configuration than existing
connections
```

When it fires:
- `_fetch_category_chain_qids()` returns `{}`.
- `tag_disciplines` short-circuits at `if not qid_by_category: return` (`rule_based_disciplines.py:296-297`).
- The repo gets no `pulse:discipline` tag — silent data-quality loss. The exception is
  caught and logged (`rule_based_disciplines.py:201-203`), so extraction still "succeeds".
- Also contributes to throughput degradation / connection resets under concurrency,
  because the failing opener races against an already-open handle on the same file.

## Root cause (verified)

DuckDB (1.5.3, project pins `duckdb>=1.0`) refuses to let a **single process** hold two
connections to the same database file under different access-mode/config. One handle is
**read-write**, the other is **read-only** → the second open throws the Connection Error.

I traced every site that opens the epfl_graph DuckDB file and confirmed the configs.
**Important correction to the report's premise:** the RAG semantic-search path inside
`tag_disciplines` does **not** open DuckDB at all — it goes through Qdrant. So within a
single `tag_disciplines` call there is exactly **one** DuckDB opener, and it is read-only.
The conflicting **read-write** handle is opened by a *different* code path in the same
process (stats endpoint and/or the federated/semantic-search path), not by the RAG search.

### Openers of the epfl_graph DuckDB file (file:line + exact config)

1. **`EpflGraphStore.connect()`** — `src/index/epfl_graph/storage/duckdb_store.py:50`
   ```python
   self._conn = duckdb.connect(str(self.db_path))     # READ-WRITE, no config dict
   ```
   Cached on `self._conn` (line 48-51); reused by every read/write method on the store.

2. **`EpflGraphStore.read_only()`** — `src/index/epfl_graph/storage/duckdb_store.py:63`
   ```python
   ro = duckdb.connect(str(self.db_path), read_only=True)   # READ-ONLY, no config dict
   ```
   Context-managed, closed on exit. (Not on the disciplines-stage hot path; listed for
   completeness — it is itself an inconsistent-config opener relative to #1.)

3. **`EpflGraphStore.bootstrap()` → `connect().execute(_load_schema_sql())`** —
   `duckdb_store.py:53-54`, schema text from `_load_schema_sql()` (line 28-29).
   This is the **only DDL writer** and it runs through the read-write `connect()` handle.
   Critically, `EpflGraphStore.open()` (line 39-45) calls `bootstrap()` on **every** open:
   ```python
   @classmethod
   def open(cls, db_path=None) -> EpflGraphStore:
       store = cls(db_path)
       store.bootstrap()      # <-- DDL on open, requires read-write
       return store
   ```

4. **`_fetch_category_chain_qids()`** — `src/v2/pipeline/stages/rule_based_disciplines.py:200`
   ```python
   con = duckdb.connect(db_path, read_only=True)     # READ-ONLY, no config dict
   ```
   Wrapped in try/except that logs the exact warning from the symptom
   (`rule_based_disciplines.py:201-203`). This is the **only** DuckDB opener on the
   disciplines hot path, and it is read-only. Invoked via `asyncio.to_thread(...)`
   (`rule_based_disciplines.py:291-295`), so concurrent extractions run it on the shared
   process's thread pool — same process, overlapping in time.

### Where the conflicting READ-WRITE handle comes from (same process)

`EpflGraphStore.open()` (read-write via `connect()`, plus `bootstrap()` DDL) is reached
during a live API process by:

- **Stats endpoint** — `src/v2/api.py:4022` `fetch_store_for_stats("epfl_graph", ...)`
  → `src/v2/indices/stats.py:270-274` `_cli_store(..., "v2_epfl_graph_store", ...)`
  → `stats.py:293` `store = store_cls.open()` (read-write) → cached on
  `app_state.v2_epfl_graph_store` (`stats.py:296-300`). This handle is **long-lived**:
  it stays open on app state until explicitly closed (`compact.py:192`,
  `close_cached_resources_for`). While it is cached open, **any** read-only opener of the
  same file in the same process — i.e. `_fetch_category_chain_qids` — hits the config
  mismatch.

- **Federated / semantic search** — `src/index/_federated/adapters/epfl_graph.py:46`
  `semantic_search(...)` → `src/index/epfl_graph/retrieval/semantic.py:148`
  `EpflGraphStore.open(config.paths.duckdb_path)` (read-write), and
  `adapters/epfl_graph.py:100` `lookup()` → `EpflGraphStore.open()` (read-write). These
  are short-lived (`finally: store.close()`), but any overlap with a `_fetch_category_chain_qids`
  read-only open trips the same error.

### Why workers=1 hides it, workers>1 exposes it
At `max_workers=1` the read-only `_fetch_category_chain_qids` open and any read-write open
never overlap in wall-clock time, so DuckDB sees a single config at a time. At workers>1,
`asyncio.to_thread`-dispatched read-only opens overlap with each other and with a cached
read-write `v2_epfl_graph_store` handle → "different configuration than existing
connections". The "one warning per worker" pattern matches each concurrent
`_fetch_category_chain_qids` losing the race against the resident read-write handle.

### Confirmed: no write/DDL is needed at inference time
On the disciplines hot path the only statement executed is a `SELECT`
(`rule_based_disciplines.py:209-212`). `_fetch_category_chain_qids` performs zero writes
and zero DDL. The only writers are `bootstrap()`/`_load_schema_sql()` (DDL) and the
`upsert_*`/`update_*` methods — all of which belong to ingest, not extraction.

## Affected code (file:line)
- `src/index/epfl_graph/storage/duckdb_store.py:47-51` — `connect()` opens read-write, caches `self._conn`.
- `src/index/epfl_graph/storage/duckdb_store.py:39-45` — `open()` always runs `bootstrap()` (DDL) on open.
- `src/index/epfl_graph/storage/duckdb_store.py:53-54`, `:28-29` — `bootstrap()` / `_load_schema_sql()` (only DDL writer).
- `src/index/epfl_graph/storage/duckdb_store.py:61-67` — `read_only()` opens read-only.
- `src/v2/pipeline/stages/rule_based_disciplines.py:185-221` — `_fetch_category_chain_qids()`; open at `:200`, warning at `:201-203`, SELECT at `:209-212`.
- `src/v2/pipeline/stages/rule_based_disciplines.py:291-297` — dispatch via `asyncio.to_thread`, `{}` short-circuit.
- `src/v2/indices/stats.py:270-300` — `_cli_store` opens read-write `EpflGraphStore.open()` and caches it on `app_state.v2_epfl_graph_store` (the resident RW handle).
- `src/v2/api.py:4022-4028` — stats endpoint that triggers the cached RW store.
- `src/index/epfl_graph/retrieval/semantic.py:148` and `src/index/_federated/adapters/epfl_graph.py:100` — federated/search RW opens (short-lived, secondary contributors).

## Proposed fix (chosen)
**Option #1 — make every inference-time open read-only with an identical config; gate
DDL/writes to ingest-time only.** DuckDB allows unlimited concurrent read-only connections
to one file *as long as no read-write connection is held in the same process*, so the rule
is: during extraction, no path may open the file read-write.

Concretely:

1. **Standardize a single connection helper and one shared `config=` dict.** All openers
   must pass byte-identical args. Introduce a module-level constant in
   `duckdb_store.py`:
   ```python
   # Identical config for every connection to the epfl_graph DB so DuckDB never
   # sees a "different configuration than existing connections".
   _DUCKDB_CONFIG: dict[str, str] = {}   # keep empty unless a setting is truly needed
   ```
   and have **both** `connect()` and `read_only()` pass `config=_DUCKDB_CONFIG`. The
   inference opener in `rule_based_disciplines.py:200` must pass the *same* dict
   (import the constant, or better, route through the store — see step 3). The key
   invariant: same `config=` dict object/contents **and** consistent `read_only` per
   process at any instant.

2. **Add an explicit read-only store factory and stop `open()` from doing DDL at
   inference time.** Split `open()`:
   ```python
   @classmethod
   def open_readonly(cls, db_path: Path | None = None) -> EpflGraphStore:
       if db_path is None:
           db_path = get_epfl_graph_paths().duckdb_path
       store = cls(db_path)
       store._read_only = True          # connect() will use read_only=True
       return store                     # NO bootstrap()/DDL

   @classmethod
   def open(cls, db_path=None) -> EpflGraphStore:   # ingest-time, unchanged semantics
       ...
       store.bootstrap()
       return store
   ```
   `connect()` becomes config-aware:
   ```python
   def connect(self) -> duckdb.DuckDBPyConnection:
       if self._conn is None:
           self.db_path.parent.mkdir(parents=True, exist_ok=True)
           self._conn = duckdb.connect(
               str(self.db_path),
               read_only=getattr(self, "_read_only", False),
               config=_DUCKDB_CONFIG,
           )
       return self._conn
   ```
   A read-only store must never reach `bootstrap()`/`upsert_*`/`update_*`; guard those to
   raise if `self._read_only` is True (cheap assert) so a misuse fails loudly at ingest
   wiring time, not silently at runtime.

3. **Point every inference/extraction caller at the read-only path.**
   - `_fetch_category_chain_qids` (`rule_based_disciplines.py:200`): pass
     `read_only=True, config=_DUCKDB_CONFIG` on its raw `duckdb.connect`, *or* obtain a
     read-only handle from the store so the config is guaranteed identical. Keep it a
     fresh per-call read-only connection (cheap, and read-only connections don't conflict).
   - **Stats path (the resident RW handle — the real trigger):** `_cli_store`
     (`stats.py:293`) currently calls `store_cls.open()` (read-write). For epfl_graph,
     switch the cached stats handle to `open_readonly()` so `app_state.v2_epfl_graph_store`
     is read-only. Stats only runs `SELECT COUNT(*)` (`duckdb_store.py:267-277`), so it
     never needs write. This removes the long-lived RW handle that read-only openers
     collide with. (Either special-case epfl_graph in `_cli_store`, or add a per-provider
     "read-only-for-stats" flag.)
   - **Federated/semantic search:** `semantic.py:148` and `adapters/epfl_graph.py:100`
     use the store only for `fetch_category()` SELECTs (`semantic.py:_walk_chain`,
     `adapters/epfl_graph.py:104`). Switch both to `EpflGraphStore.open_readonly(...)`.

4. **Net effect:** at extraction time the file is only ever opened **read-only** with the
   **same `config={}`**, across all of: `_fetch_category_chain_qids`, stats, federated
   search. DuckDB then permits arbitrary concurrent opens → warning gone, multi-worker
   extraction safe, `pulse:discipline` populated.

### Callers that currently rely on read-write at extraction time
None on the disciplines hot path (verified: SELECT only). The only read-write needs are
ingest (`cli.py:51/69/88/119`, `ingest/download.py:158`, `ingest/wikidata_qids.py`,
`ingest/wikipedia_extracts.py`, `embed/pipeline.py`) — all of which keep calling `open()`
unchanged. The stats endpoint *currently* opens RW but does not *need* RW; moving it to
read-only is the fix, not a regression.

## Alternatives considered
- **#2 — single shared connection reused for both RAG search and category-chain lookup.**
  Rejected as the primary fix: the RAG search path doesn't touch DuckDB at all (it's
  Qdrant), so there is nothing to share there. A process-wide shared `EpflGraphStore`
  singleton for *all* DuckDB readers could work, but a single connection is not safe to
  use concurrently from multiple `asyncio.to_thread` workers (DuckDB connections aren't
  thread-safe for simultaneous use), so it would need its own lock — collapsing into
  option #3. More invasive than #1, and still requires gating DDL. Possible secondary
  optimization later (one cached read-only connection for stats + lookups) once #1 lands.
- **#3 — serialize/pool epfl_graph DB access behind a lock/semaphore.** Works, but
  re-serializes exactly the concurrency we want to unlock, hurting throughput. It also
  doesn't fix the underlying inconsistency (a stray RW open elsewhere still breaks it).
  Keep as a defensive fallback only if some path genuinely must write during extraction
  (none found).

## Risks & backward-compat
- **Low risk.** Behavior change is confined to *which mode* the file is opened in for
  read-only consumers; no schema/data change.
- A read-only `connect()` fails if the DB file does not yet exist (DuckDB won't create an
  empty file in read-only mode). The disciplines stage already tolerates this: the open is
  wrapped in try/except returning `{}` (`rule_based_disciplines.py:201-203`). For stats,
  ensure the read-only path is only taken when the file exists, else fall back gracefully
  (stats already catches exceptions). Worth an explicit "exists?" check before
  `open_readonly()` in `_cli_store` for epfl_graph.
- The cached `app_state.v2_epfl_graph_store` becoming read-only means any *future* code
  that tries to write through the stats-cached handle would fail — acceptable and
  desirable (stats must not write). The `_read_only` guard on `bootstrap`/`upsert`/`update`
  surfaces such misuse immediately.
- `config=_DUCKDB_CONFIG` must be passed *everywhere* (including `read_only()` and the raw
  open in `_fetch_category_chain_qids`); a single missed call site re-introduces a config
  mismatch. Add a brief comment at each `duckdb.connect` site pointing to this invariant.

## Test / verification plan
1. **Repro (pre-fix).** With a populated epfl_graph DuckDB + Qdrant collection and
   `V2_EPFL_GRAPH_RAG_ENABLED=1`:
   - First hit the stats endpoint (`/indices/overview` or the epfl_graph stats path) so
     `app_state.v2_epfl_graph_store` is opened read-write and cached.
   - Then run a **batch extraction of ≥3 repos with ≥2 concurrent workers**. Confirm the
     warning `rule_based_disciplines: duckdb open failed — Connection Error: Can't open a
     connection to same database file with a different configuration` appears (≈once per
     concurrent worker) and the affected repos have no `pulse:discipline`.
2. **Unit test for `_fetch_category_chain_qids` concurrency.** New test that opens a
   read-write `duckdb.connect(path)` on the test DB, holds it, then calls
   `_fetch_category_chain_qids(ids, path)` from N threads. Pre-fix: warnings + `{}`.
   Post-fix: all return the expected qid/parent map, no warning logged
   (assert via `caplog` that no "duckdb open failed" record is emitted).
3. **Store-mode test.** Assert `EpflGraphStore.open_readonly(path).connect()` is read-only
   (a write/DDL raises) and that two read-only stores + one read-only raw connection can
   coexist in-process on the same file without error.
4. **End-to-end (post-fix).** Repeat step 1's batch with ≥2 workers (stats endpoint warmed
   first). Assert: (a) zero "duckdb open failed" warnings in logs; (b) every repository
   entity that should match has a non-empty `pulse:discipline` list; (c)
   `rule_based_disciplines: emitted=N` log (`api.py:1580`) shows N>0 for matching repos;
   (d) throughput is not serialized (workers run in parallel).
5. **Ingest regression.** Run `python -m src.index.epfl_graph ...` ingest commands
   (`cli.py`) to confirm `open()`/`bootstrap()`/upserts still work read-write.

## Effort estimate
~0.5–1 day. Core change is small (one connection helper + `open_readonly` + ~3 call-site
switches), but it spans the store, the stats `_cli_store` path, and the federated/semantic
search opens, each needing a test. Most effort is the concurrency repro test and verifying
no other process-resident RW handle remains.

## Open questions
- Should the read-only-for-stats behavior be epfl_graph-specific in `_cli_store`, or
  generalized (a per-provider "stats is read-only" flag)? Other CLI stores
  (`v2_infoscience_store`, `v2_snsf_store`, `v2_ror_store`) may have the same latent issue
  if their DuckDB files are also opened read-only elsewhere during extraction — worth a
  quick audit.
- Does any deployment open the epfl_graph DB read-write for a legitimate extraction-time
  reason (e.g. wikipedia enrichment running alongside extraction)? None found in v2, but
  confirm no out-of-band job writes to the same file during serving.
- Should `_fetch_category_chain_qids` route through `EpflGraphStore.open_readonly()` (so
  the `config=` dict is guaranteed shared) instead of its own raw `duckdb.connect`? Slight
  layering cost vs. a stronger consistency guarantee — recommend routing through the store.
