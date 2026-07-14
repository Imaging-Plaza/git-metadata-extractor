# Task 05 — Enforce a single-writer operation boundary

**Severity:** P1 · **Status:** Design then implementation · **Repositories:** both

## Objective

Prevent concurrent DuckDB/Qdrant mutations across parent workers, child service
workers, migration jobs, and maintenance commands.

## Current topology

- `gme-api` and `gme-sources` share `gme-data` and Qdrant.
- Child owns bulk ingest/embed/reset/compact through its service.
- Parent retains four opt-in direct writers in `src/v2/api.py`:
  GitHub repos, users, organizations, and HuggingFace papers.
- Parent Gunicorn startup calls child federated bootstrap.
- Parent locks are process-local and cannot coordinate with child workers.
- Child service schedules independent background ingest tasks.
- Child ingest pool defaults to two concurrent tasks.
- The same service also exposes destructive reset/re-embed operations.
- Compose comments warn operators about contention but do not enforce safety.

Auto-ingest defaults off, reducing default risk, but this remains an
operator-dependent boundary rather than an architectural guarantee.

## Preferred design

Make `gme-sources` the only write owner:

1. Parent auto-ingest posts authenticated jobs to the child service, or is
   removed.
2. Store bootstrap/schema migration runs in the child service or an init job,
   not parent Gunicorn startup.
3. Child serializes operations per provider/store.
4. Destructive reset, migration, compact, full re-embed, and ingest share the
   same provider-scoped coordination.

If direct library writes must remain, implement a real cross-process lock with
documented crash recovery; process-local `asyncio.Lock` is insufficient.

## Implementation steps

1. Enumerate every mutating entry point in both repositories.
2. Define operation classes and conflict rules per provider.
3. Introduce provider-scoped serialization in the child service.
4. Decide lock scope across multiple service workers/replicas:
   - file lock for shared-volume single host,
   - database/distributed lease for multi-host,
   - or strict single-worker operational constraint.
5. Rewire or remove parent auto-ingest direct writes.
6. Move parent startup bootstrap ownership.
7. Ensure reset/re-embed cannot race with search-visible collection updates.
8. Add idempotency keys/job de-duplication where repeated requests are likely.
9. Expose clear queued/running/conflict status and logs.
10. Update compose defaults to enforce, not merely suggest, the chosen policy.

## Tests

- Concurrent same-provider ingests serialize.
- Different independent providers may run concurrently if safe.
- Reset waits for or rejects conflicting ingest/re-embed.
- Parent auto-ingest and manual child ingest cannot write simultaneously.
- Lock release works after failure/cancellation.
- Multi-process test proves the mechanism is not process-local only.

## Acceptance criteria

- Exactly one authority can mutate a provider store/collection at a time.
- Parent extraction remains available during child ingest.
- Operators cannot accidentally enable an unsafe dual-writer topology.
- Failure and restart behavior is documented and tested.

