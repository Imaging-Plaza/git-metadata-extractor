# Task 07 — Harden the monolith-to-split migration utility

**Severity:** P1 · **Status:** Ready · **Repository:** `open-pulse-sources/`

## Objective

Make `scripts/migrate_monolith_to_split.py` portable, testable, fail-loud, and
safe for real store migration.

## Confirmed problems

- Script documentation still references its old `scripts/v2/` location.
- `_PROJECT_ROOT = Path(__file__).resolve().parents[2]` assumes the old depth.
  The script now lives directly under `scripts/`, so it resolves above the
  child repository.
- In the child Docker image (`/app/scripts/...`), this can resolve data under
  `/data/index` instead of `/app/data/index`.
- Missing stores, lock failures, and embed failures can be reported while the
  process still exits successfully.
- No focused tests reference this split migration script.
- Parent runbook currently shows:

  ```bash
  python scripts/migrate_monolith_to_split.py  # (...) --apply --reembed
  ```

  Everything after `#` is ignored by the shell, so the documented apply command
  performs only its default behavior.

## Implementation steps

1. Replace implicit repository-root discovery with explicit CLI parameters and
   environment-aware defaults:
   - source monolith data root,
   - target split data root,
   - config root,
   - Qdrant endpoint.
2. Resolve defaults relative to the actual child project/container layout.
3. Validate source and target paths before any write.
4. Preserve dry-run as the default.
5. Return nonzero on missing required stores, lock failures, row-count
   mismatch, embed failure, or snapshot publication failure.
6. Produce a machine-readable summary plus concise operator output.
7. Verify row counts/checksums per migrated table.
8. Make reruns idempotent and document rollback/recovery.
9. Coordinate destructive re-embed and collection drops with Task 05 locks.
10. Publish the final read-only snapshots after successful mutation.
11. Correct script help, README, and parent operations runbook.

## Tests

- Root/path resolution from repository checkout and Docker-like `/app`.
- Dry run performs zero writes.
- Apply copies expected fixture rows and is idempotent.
- `--reembed` invokes the expected collection workflow.
- Missing source and lock conflict return nonzero.
- Partial failure leaves target recoverable and does not publish a bad snapshot.
- Row-count mismatch fails.
- CLI arguments after comments are not used in docs; test documented command
  examples where practical.

## Acceptance criteria

- The same explicit command works from checkout and sources container.
- Failure always produces a nonzero exit and actionable summary.
- Successful migration verifies data, embeddings, and snapshot publication.
- Runbook commands can be copied exactly and perform the stated operation.

