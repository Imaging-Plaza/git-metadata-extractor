# Bug 13 — github_organizations index representational inconsistency

**Severity:** low (observability/consistency) · **Status:** Investigated — plan ready (no code changed) · **Area:** index storage consistency

## Symptom

As reported: `github_organizations` is alleged to write embeddings straight to Qdrant
with **no DuckDB `chunks` table**, unlike `github_users` / `github_repos` (which keep a
DuckDB `chunks` table). The reported impact is verification/observability inconsistency
between the three GitHub indices (harmless, no functional effect on retrieval).

## Findings (org vs users/repos storage, file:line)

Investigating the real code shows the three indices are already representationally
consistent. The reported divergence does **not** exist at the source level. Concretely:

**1. All three schemas define a `chunks` table (identical contract).**
- `src/index/github_organizations/storage/schema.sql:31-40` — `CREATE TABLE IF NOT EXISTS chunks (...)` with `entity_type` commented `"organizations"`, plus `src/index/github_organizations/storage/schema.sql:44` index `idx_chunks_entity`.
- `src/index/github_users/storage/schema.sql:31-40` — same `chunks` table (`entity_type "users"`), index at `:45`.
- `src/index/github_repos/storage/schema.sql:33-42` — same `chunks` table (`entity_type "repos"`), index at `:48`.

The `chunks` table columns are byte-for-byte identical across all three:
`chunk_id` (PK), `entity_type`, `entity_id`, `chunk_index`, `text`, `token_count`,
`vector_id`, `embedded_at`.

**2. The org embed pipeline persists chunks to DuckDB — it does NOT write "straight to Qdrant".**
- `src/index/github_organizations/embed/pipeline.py:66-78` — `embed_organizations` delegates to the shared `embed_accounts_async(...)`.
- `src/index/github_users/embed/pipeline.py:61-73` — `embed_users` delegates to the **same** `embed_accounts_async(...)`.
- The shared flush loop performs Qdrant upsert **then** DuckDB chunk persistence:
  `src/index/_github_accounts_base/embed_base.py:119-124` (Qdrant `upsert_points`) followed by
  `src/index/_github_accounts_base/embed_base.py:135-145` (`upsert_chunk(conn, ...)` into the `chunks` table).
  So orgs persist chunks via the identical code path users do.

**3. The org store exposes `upsert_chunk` and bootstraps the schema, same as users.**
- `src/index/github_organizations/storage/duckdb_store.py:140-160` — `GitHubOrganizationsStore.upsert_chunk` wraps the shared `upsert_chunk`.
- `src/index/github_organizations/storage/duckdb_store.py:59-60` — `bootstrap()` applies `schema.sql` (which creates `chunks`).
- Mirror in users: `src/index/github_users/storage/duckdb_store.py:132-152` (`upsert_chunk`) and `:57-58` (`bootstrap`).
- The shared chunk write/stream/upsert helpers live in `src/index/_github_accounts_base/storage_base.py:89-114` (`upsert_chunk`) and `:56-86` (`stream_unembedded`, which the `chunks` table is the join target for re-embed skipping).

**4. Repos differs only in mechanics, not in the `chunks` contract.**
- `github_repos` has its own (non-shared) embed pipeline that inlines the same
  Qdrant-then-DuckDB ordering: `src/index/github_repos/embed/pipeline.py:169-190`
  (Qdrant upsert at `:169`, `store.upsert_chunk(...)` at `:181-190`).
- Repos additionally provides `rebuild_qdrant_from_chunks` (`src/index/github_repos/embed/pipeline.py:301-312`)
  which re-derives Qdrant points from the DuckDB `chunks` table after a Qdrant wipe.
  This is the one capability orgs/users do **not** have (see Open questions) — but it
  is unrelated to the reported "no chunks table" symptom, since it *relies on* the
  chunks table that orgs already maintain.

**5. The only place `chunks` is deliberately treated differently is the read-only snapshot, and that is uniform across all indices.**
- `src/index/_snapshot.py:39` — `SNAPSHOT_SKIP_TABLES = frozenset({"chunks"})`; the
  `.ro.duckdb` publication intentionally skips `chunks` for *every* provider (it is
  embedding bookkeeping, never served). This applies equally to orgs, users, and repos,
  so it is not a source of org-vs-users divergence.

## Root cause (missing DuckDB chunks step)

There is **no missing DuckDB chunks step**. The premise of Bug 13 — that
`github_organizations` writes to Qdrant without persisting a `chunks` table — is not
borne out by the code:

- The `chunks` table exists in the org schema (`schema.sql:31-40`).
- The org embed path persists every chunk to that table via the shared
  `embed_accounts_async` → `upsert_chunk` (`embed_base.py:135-145`).
- `stream_unembedded` (`storage_base.py:56-86`) reads `chunks` back to skip
  already-embedded orgs, proving the table is both written and read in the org flow.

Most likely the report predates the D1/D3 refactor (`ccee49c` "shared base for github
user + org account indices", `c004601` "github_organizations index — DuckDB + Qdrant")
that unified orgs and users onto the shared base, or it was filed against a different
index family. As of the current tree, orgs and users share the exact same chunk
persistence code.

## Proposed fix (mirror users/repos)

No source change is required to add a `chunks` table — it already exists and is already
written. Recommended action: **close Bug 13 as "not reproducible / already consistent"**
after the (cheap) runtime verification below.

If, on a live deployment, an org store is found whose `chunks` table is empty while
Qdrant holds org points, that indicates **data-state drift** (orgs embedded under an
older code revision before the shared base landed), not a code defect — handle it via
the backfill in the next section rather than a code edit.

Optional, genuinely-additive consistency improvement (not required by this bug, and the
only real gap found): give orgs and users a `rebuild_qdrant_from_chunks` entry point to
match `github_repos` (`src/index/github_repos/embed/pipeline.py:301-312`). Concrete
target: add a shared `rebuild_accounts_from_chunks(...)` helper in
`src/index/_github_accounts_base/embed_base.py` and thin wrappers in
`src/index/github_organizations/embed/pipeline.py` and
`src/index/github_users/embed/pipeline.py`. This would close the *actual* asymmetry
(orgs/users cannot rebuild Qdrant from their chunks after a wipe; repos can) and is
strictly observability/recoverability, not a behavior change. Treat as a separate
low-priority enhancement, not part of this bug.

## Migration / backfill consideration

No backfill is needed for any data ingested/embedded under the current code, because the
chunks are written inline during embed (`embed_base.py:135-145`).

Backfill is **only** relevant if a production org store was embedded under a pre-shared-base
revision and therefore has Qdrant points but no `chunks` rows. To detect and remediate:

1. Detect: `SELECT count(*) FROM chunks WHERE entity_type='organizations'` on the live
   org `*.duckdb` and compare against the Qdrant `github_organizations` collection point
   count. A non-trivial Qdrant count with zero/low chunk rows confirms drift.
2. Remediate (no code change): re-run the normal embed for orgs. Because
   `stream_unembedded` (`storage_base.py:56-86`) selects rows with **no** matching
   `chunks` entry, a re-embed will re-chunk and re-persist those orgs and upsert
   Qdrant points under the same deterministic `chunk_id`
   (`embed_base.py:43-46`, `uuid5(entity_type|entity_id|index)`), so it is idempotent and
   safe — no duplicate Qdrant points, no Qdrant wipe required.

Recommendation: do **not** schedule a blanket backfill. Run the detection query first;
only re-embed if drift is actually observed.

## Risks & considerations

- Closing as "not a bug" risks dismissing a real data-state issue if one exists in prod;
  mitigate by running the detection query (above) before closing.
- The deterministic `chunk_id` and `ON CONFLICT (chunk_id) DO UPDATE`
  (`storage_base.py:106-113`) make any re-embed idempotent, so remediation is low-risk.
- The snapshot deliberately omits `chunks` (`_snapshot.py:39`). Anyone using the
  `.ro.duckdb` snapshot to "verify" chunk persistence will see an empty/absent `chunks`
  table and could misread it as the bug — verification must run against the **live**
  `*.duckdb`, not the snapshot. This is the most plausible origin of the report.

## Test / verification plan

1. Static confirmation (done in this investigation): org schema has `chunks`
   (`schema.sql:31-40`); org embed calls `upsert_chunk` via the shared base
   (`embed_base.py:135-145`).
2. Runtime confirmation: ingest one org, run `embed_organizations`, then query the live
   org DuckDB: `SELECT count(*) FROM chunks WHERE entity_type='organizations'` must be > 0,
   and the row count should match the Qdrant `github_organizations` point count.
3. Parity check: run the same ingest+embed+count for `github_users` and confirm both
   indices populate `chunks` identically.
4. Existing tests: search `tests/index/` for any embed/chunks assertions on the github
   account indices and confirm they cover orgs (add an org-specific assertion only if the
   users test exists but the org one does not — that would be the only justified code
   change, and it is test-only).

## Effort estimate

- Verification + closing the bug as already-consistent: ~30 min (mostly the runtime
  query in step 2/3).
- Optional `rebuild_qdrant_from_chunks` parity enhancement for orgs/users (separate
  ticket): ~2–3 h including a shared helper + wrappers + tests.
- Conditional prod backfill (only if drift detected): ~1 h to run + verify; no code.

## Open questions

- Was Bug 13 filed before the D1/D3 shared-base refactor (`ccee49c` / `c004601`)? If so it
  is stale and can be closed on that basis.
- Does any production `github_organizations.duckdb` actually show empty `chunks` with
  populated Qdrant? Needs the detection query against live data to settle.
- Is the *real* desired outcome the `rebuild_qdrant_from_chunks` parity (the one genuine
  asymmetry: repos can rebuild from chunks, orgs/users cannot)? If yes, retarget this
  ticket to that enhancement.
