# Task 06 — Complete snapshot-based DuckDB reads

**Severity:** P1 · **Status:** Coordinate with Task 05 · **Repositories:** both

## Objective

Ensure extraction reads stable read-only snapshots while the sources service
mutates live DuckDB stores.

## Existing strength

The child already provides atomic snapshot publication:

- `open_pulse_sources/index/_snapshot.py`
- separate-process coverage in `tests/index/test_snapshot.py`
- service embed-step publication coverage in
  `tests/service/test_embed_step_snapshot.py`

This is a sound reader/writer isolation mechanism when all readers use the
published `.ro.duckdb` file and every write path republishes it.

## Confirmed gaps

Parent code still opens live DuckDB paths directly, including:

- `git_metadata_extractor/pipeline/stages/refine_with_llm.py:1106` and `:1154`
- `git_metadata_extractor/providers/snsf_grants.py:80`, `:105`, `:127`

*(Paths re-verified 2026-07-29 after the `src/v2` → `git_metadata_extractor`
rename; every call is still a `duckdb.connect(..., read_only=True)` against
the live store rather than the published `.ro.duckdb` snapshot.)*

Standalone child CLIs and maintenance paths are not uniformly proven to publish
snapshots. A live-path read can fail, block, or observe partial state during a
child write.

## Implementation steps

1. Inventory all DuckDB opens in both repositories.
2. Classify each as:
   - canonical writer,
   - snapshot publisher,
   - serving reader,
   - migration/maintenance reader.
3. Change parent serving reads to:
   - `.ro.duckdb` snapshots, or
   - child HTTP hydration/query endpoints.
4. Centralize path selection in a child public helper instead of duplicating
   filename logic in parent code.
5. Ensure every successful mutating path republishes atomically:
   - ingest,
   - embed,
   - compact,
   - migration,
   - repair/backfill,
   - standalone CLI operations.
6. Define behavior when no snapshot exists:
   - explicit unavailable result,
   - safe initial publication,
   - never silently read the live writer file.
7. Add snapshot freshness metadata and observability.
8. Coordinate publication with Task 05 operation locks.

## Tests

- Child writer mutates live DB while parent repeatedly reads snapshot.
- Snapshot replacement is atomic across processes.
- Failed writes do not replace the last good snapshot.
- Every mutating command publishes or explicitly documents why it does not.
- Missing/stale snapshot behavior is deterministic and logged.
- Parent SNSF/refiner paths never open a live writer database.

## Acceptance criteria

- No latency-sensitive parent request opens child-owned live DuckDB state.
- Snapshot publication covers every supported write path.
- Readers either get the previous good snapshot or the next complete snapshot,
  never partial state.
- Freshness and failure modes are visible to operators.

