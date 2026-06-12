# Bug 12 — snsf DuckDB bootstrap "Vector::Reference used on vector of different type"
**Severity:** medium (blocks snsf index) · **Status:** Investigated — plan ready (no code changed) · **Area:** snsf index / DuckDB schema

## Symptom
Bootstrapping the `snsf` index DuckDB store raises the DuckDB internal error
`Vector::Reference used on vector of different type`. The snsf store is opened
and schema-applied by `SnsfStore.open()` → `SnsfStore.bootstrap()`
(`src/index/snsf/storage/duckdb_store.py:86`), and the federated bootstrap then
runs the snsf post-bootstrap hook `build_facets`
(`src/index/_federated/bootstrap.py:29-37`, `:113-115`). `Vector::Reference …`
is a low-level DuckDB *execution-engine* assertion (not a Binder/Conversion
error): the engine tried to `Reference` one vector into another whose physical
type differs — i.e. an expression produced a column whose runtime type does not
match the type DuckDB bound the surrounding INSERT/SELECT/list-lambda against.
It is raised when a typed expression (a `LIST_TRANSFORM` lambda, a `CASE`, a
JSON cast, an `INSERT … BY NAME`, or a multi-chunk `read_csv_auto`) yields a
vector of a different physical type than its slot for at least one chunk/row.

## Findings (bootstrap SQL / inserts, file:line)
The full snsf "bootstrap" is three layers, all of which run typed SQL:

1. **Schema DDL** — `src/index/snsf/storage/schema.sql` (loaded at
   `duckdb_store.py:61-62, 88`). Declares `grants.grant_number TEXT PRIMARY KEY`
   (`schema.sql:11`), `persons.*_grants JSON` (`schema.sql:85-91`), the seven
   `output_*` tables with `grant_number TEXT` FK, and the facet tables. Pure DDL
   with `IF NOT EXISTS` — not itself a source of the vector error.

2. **In-place legacy migration** — `SnsfStore._migrate_grant_ids_to_url`
   (`duckdb_store.py:98-151`). **This runs only when `grants.grant_number` is
   still `INTEGER`** (gate at `:114-119`). The current on-disk DB
   `data/index/snsf/duckdb/snsf.duckdb` *is* pre-v3 (verified:
   `grants.grant_number = INTEGER`, `scope_records.grant_number = INTEGER`,
   `output_publications.grant_number = INTEGER`), so the migration **does fire**
   on the next bootstrap. It:
   - rebuilds `grants` + 8 FK tables via
     `CREATE TEMP TABLE _mig_<t> AS SELECT * REPLACE (<url_sql> AS grant_number) FROM <t>`
     (`:131-134`), `DROP TABLE` (`:135`), re-`_load_schema_sql()` (`:136`), then
     `INSERT INTO <t> BY NAME SELECT * FROM _mig_<t>` (`:137-141`);
   - rewrites the seven per-role JSON arrays in `persons` (`:146-151`):
     `UPDATE persons SET <col> = TO_JSON(LIST_TRANSFORM(CAST(<col> AS BIGINT[]), n -> '<base>' || n)) WHERE <col> IS NOT NULL`.
   The `url_sql` fragment is `snsf_grant_iri_sql` (`src/v2/canonicalization/snsf.py:80-96`):
   a 4-branch `CASE` over `CAST(expr AS VARCHAR)`.

3. **Facet build (post-bootstrap hook)** — `build_facets`
   (`src/index/snsf/facets.py:34-147`), DDL from `storage/facets.sql`. It expands
   the migrated person arrays with `json_each(p.<col>)` and
   `json_extract_string(j.value,'$')` into `grant_persons`
   (`facets.py:57-69`), where `grant_persons.grant_number` is `NOT NULL`
   (`schema.sql:275-280`).

4. **CSV bulk loaders** (`load_grants` `:179-232`, `load_persons` `:234-293`,
   `_replace_output_table` `:476-494`) run on ingest, not on `bootstrap()`, but
   share the same typed-expression patterns (`read_csv_auto(..., sample_size=-1)`,
   the `_split` lambda `:253-261`, the DOI `CASE` `:349-362`).

**Empirical grounding (DuckDB 1.5.3, the pinned/installed version —
`pyproject.toml:55` `duckdb>=1.0`, `uv.lock` resolves 1.5.3):**
- `bootstrap()` on a copy of the real (empty-rows) DB → **succeeds**.
- A reconstructed *full-schema* pre-v3 fixture (INTEGER PK/FKs + JSON int
  arrays, all columns present, rows seeded) → migration **succeeds**; `grants`,
  FK tables, and `persons` arrays all promote to URLs.
- Whole snsf test suite `tests/index/snsf/` → **71 passed**, incl.
  `test_grant_id_canonical_url.py::test_migration_promotes_legacy_integer_ids`.

So on **1.5.3 the exact internal error did not reproduce** in any path I could
construct. What *did* reproduce are adjacent, data-dependent failures in the
same statements (see next two sections), which on a different DuckDB
build/version or specific data shape are the realistic source of the reported
internal assertion.

## Likely offending statement & root cause
Ranked by likelihood, with the grounded reasoning:

**(A — leading) The `persons` JSON-array migration UPDATE**
(`duckdb_store.py:146-151`):
```sql
UPDATE persons SET <col> = TO_JSON(LIST_TRANSFORM(
    CAST(<col> AS BIGINT[]), n -> '<base>' || n)) WHERE <col> IS NOT NULL
```
`CAST(<json> AS BIGINT[])` forces every array element to `BIGINT`. This is the
single most type-fragile statement in the bootstrap, and it is **not
idempotent**: I reproduced `Conversion Error: Failed to cast value to numerical`
when an element is a non-numeric token, and when the array already holds URL
strings (`CAST('["https://data.snf.ch/grants/grant/108806"]' AS BIGINT[])`
throws). The gate at `:118` normally prevents a re-run, but any partially
migrated DB (migration interrupted after `grants` flips to VARCHAR but before/
during the `persons` loop, or persons already URL-ified by a prior `load_persons`)
leaves `grants.grant_number = VARCHAR` while `persons` arrays are mixed —
re-bootstrap skips the gate yet the data is heterogeneous. The lambda
`n -> '<base>' || n` mixes a `BIGINT` element into a `VARCHAR` concat; if a
chunk's `n` vector is bound `BIGINT` but materializes a string element, that is
exactly the "Reference used on vector of different type" shape.

**(B) `INSERT INTO <t> BY NAME SELECT * FROM _mig_<t>`** (`:137-141`). After
`DROP TABLE` + schema re-create, the temp's column *types* are whatever
`SELECT * REPLACE (...)` produced (`grant_number` now VARCHAR via `url_sql`;
all other columns inherited from the dropped legacy table). `BY NAME` matches by
column name but the engine must align each source vector to the freshly declared
schema type. If a legacy column's physical type differs from the new schema's
(e.g. a legacy `INTEGER`/`HUGEINT` count vs new `INTEGER`, a legacy `VARCHAR`
date vs new `TIMESTAMP`, a `DOUBLE` vs `BIGINT amount_granted`), `BY NAME` can
trip the vector-type assertion instead of a clean cast. The real legacy DB's
column types beyond `grant_number` are the unknown here.

**(C) `json_each` → `grant_persons` insert** (`facets.py:57-69`, runs in the
post-bootstrap hook). I reproduced a **`NOT NULL constraint failed:
grant_persons.grant_number`** when a person array contains a JSON `null`
element — which `_split` (`:253-261`) emits via its `ELSE NULL` branch for
non-numeric tokens. `json_extract_string(null,'$')` → SQL NULL → constraint
violation. Same statement, with element-type drift across rows, is a candidate
for the internal vector error on other DuckDB builds.

**Root cause (common thread):** the v3.0.0 grant-id-as-URL migration converts
`grant_number` from a homogeneous `INTEGER`/`BIGINT` domain to `VARCHAR`/URL and
rewrites JSON arrays through hard `CAST(... AS BIGINT[])` / string-concat
lambdas. These conversions assume each column is *uniformly* legacy-integer; any
already-migrated, interrupted, or mixed value violates that assumption, and the
forced cast/lambda produces a vector whose physical type disagrees with its
bound slot.

## Proposed fix (corrected SQL / binding)
Make the migration **defensive and idempotent**, and the facet build
**null-safe**:

1. **Persons array UPDATE — stop forcing BIGINT; transform per-element with an
   explicit text path** (`duckdb_store.py:146-151`). Replace:
   ```sql
   UPDATE persons SET <col> = TO_JSON(LIST_TRANSFORM(
       CAST(<col> AS BIGINT[]), n -> '<base>' || n)) WHERE <col> IS NOT NULL
   ```
   with a VARCHAR-keyed, idempotent transform that passes through already-URL
   elements and drops nulls/non-numerics:
   ```sql
   UPDATE persons SET <col> = TO_JSON(LIST_FILTER(
       LIST_TRANSFORM(
           CAST(<col> AS VARCHAR[]),
           x -> CASE
                  WHEN x IS NULL THEN NULL
                  WHEN starts_with(lower(x), '<base>') THEN x
                  WHEN regexp_full_match(x, '\d+') THEN '<base>' || x
                  ELSE NULL
                END),
       e -> e IS NOT NULL))
   WHERE <col> IS NOT NULL
   ```
   (`<base>` = `_GRANT_BASE`.) This mirrors `snsf_grant_iri_sql` semantics, never
   casts a URL to BIGINT, and is safe to re-run. Verified building block:
   `CAST(json AS VARCHAR[])` + per-element `CASE` runs on 1.5.3 without the
   BIGINT conversion error.

2. **Harden the migration gate against partial/mixed state.** The current gate
   (`:114-119`) only checks `grants.grant_number`. Either (a) make every step
   idempotent (per #1, and the `url_sql` `CASE` already is for the FK tables), or
   (b) wrap the entire `_migrate_grant_ids_to_url` body in the existing
   `store.transaction()` so an interruption rolls back and the gate stays
   consistent. Recommend both.

3. **`INSERT … BY NAME` (`:137-141`) — cast at the snapshot.** Make the temp
   snapshot project columns to the *target* schema types so `BY NAME` never has
   to reconcile a physical-type mismatch. Simplest: after recreating the schema,
   build the insert column list from `information_schema.columns` of the new
   table and `SELECT col::<target_type>` for each, instead of `SELECT *`. (Lower
   priority — only needed if the real legacy DB has off-type non-`grant_number`
   columns; confirm via the diagnosis plan.)

4. **`grant_persons` null-safety** (`facets.py:57-69`). Add
   `WHERE json_extract_string(j.value,'$') IS NOT NULL` (alongside the existing
   `p.<col> IS NOT NULL`) so a `null` array element can't violate the `NOT NULL`
   PK column. This is independently correct regardless of the vector error.

## Diagnosis plan (if not statically pinpointable)
The exact internal error did not reproduce on 1.5.3 here, so confirm the
offending statement on the *actual* failing environment before changing code:

1. **Capture the real type map of the on-disk DB** (read-only):
   ```sql
   SELECT table_name, column_name, data_type
   FROM information_schema.columns
   WHERE table_schema='main' ORDER BY 1,2;
   ```
   Compare every non-`grant_number` column against `schema.sql` to find any
   physical-type drift that `INSERT … BY NAME` (#3) would hit. Also dump a few
   `persons.*_grants` values to see if any are already URLs or hold non-numeric
   tokens (drives #1/#2).
2. **Run the bootstrap in isolation on a copy**, statement-bisected. Reproduce
   the existing repro harness: copy `data/index/snsf/duckdb/snsf.duckdb` to
   `/tmp`, then call, in order and individually: `migrate_doi_column_to_url`,
   each `CREATE TEMP … SELECT * REPLACE`, each `INSERT … BY NAME`, each persons
   `UPDATE`, then `build_facets`. The first to raise `Vector::Reference …`
   localizes it to one statement and one table/column.
3. **Confirm the DuckDB version** on the failing host. `Vector::Reference used
   on vector of different type` is characteristic of specific/older builds; this
   repo pins `duckdb>=1.0` (`pyproject.toml:55`) and resolves 1.5.3
   (`uv.lock`), but a stale wheel on the host could behave differently. Run
   `python -c "import duckdb; print(duckdb.__version__)"` there and, if it
   differs, reproduce on that exact version.
4. **Verbose error context.** Wrap the bisected statement in
   `try/except duckdb.Error` and log `conn.execute("PRAGMA version")` plus the
   statement SQL; for the lambda paths, materialize the intermediate
   (`SELECT CAST(<col> AS BIGINT[]) FROM persons LIMIT 5`) to surface the first
   offending row/element.

## Risks & considerations
- The migration mutates the live `data/index/snsf/duckdb/snsf.duckdb` in place
  by dropping/recreating `grants` + 8 FK tables. A fix that changes migration
  order or transactionality must keep it safe to re-run and must not lose rows
  on interruption — wrapping in a transaction (#2b) is the safest guard.
- `INSERT … BY NAME` (#3) hides column-type mismatches; tightening it to
  explicit casts could surface previously-silent off-type columns. Validate row
  counts before/after.
- Changing the persons transform (#1) alters stored JSON; downstream
  `build_facets` `json_each` and any consumer of the arrays must still see URL
  strings. Covered by `tests/index/snsf/test_facets.py`.
- `_split` (`:253-261`, ingest path) and `snsf_grant_iri_sql` already drop
  non-numeric tokens to `NULL`; keep all three transforms (`_split`,
  `url_sql`, the persons UPDATE) semantically identical to avoid drift.

## Test / verification plan
1. Extend `tests/index/snsf/test_grant_id_canonical_url.py`
   (`test_migration_promotes_legacy_integer_ids`, `:79`) with adversarial
   fixtures: a person array already holding URL strings; an array with a
   non-numeric / `null` element; and a *partially* migrated DB
   (`grants.grant_number` VARCHAR but `persons` arrays still bare ints) — assert
   re-running `_migrate_grant_ids_to_url` is a clean no-op / converges, with no
   `Conversion` or vector error.
2. Add a `build_facets` test where a person array contains a `null` element;
   assert `grant_persons` is populated without a `NOT NULL` violation (#4).
3. End-to-end: copy the real on-disk DB to `tmp_path`, run the full
   `SnsfStore.open()` + `build_facets` (mirroring the federated hook), assert it
   completes and `grants.grant_number` ends up VARCHAR/URL.
4. Re-run `python -m pytest tests/index/snsf/ -q` (baseline: 71 passed) and the
   federated bootstrap smoke
   `python -m src.index._federated.bootstrap --only snsf`.

## Effort estimate
- Diagnosis on the failing host (steps 1-4): ~1-2 h.
- Fixes #1 + #4 (the two empirically-confirmed fragile statements) + tests:
  ~2-3 h.
- Optional hardening #2/#3 if the real DB shows off-type columns: +1-2 h.
- **Total: ~0.5-1 day**, most of it confirming the exact failing statement on
  the real DuckDB version/data, since it did not reproduce on 1.5.3 here.

## Open questions
- **What DuckDB version and what exact data shape** produced the report? On the
  pinned 1.5.3 with the current on-disk DB I could not reproduce
  `Vector::Reference …`; the report may come from a different DuckDB build or a
  data state not present in the committed DB.
- **Does this block the whole snsf index or one table?** The grant-id migration
  is a single `bootstrap()` call that, if it throws, aborts before any table is
  usable — so it would **block the entire snsf store** (and, via the
  post-bootstrap hook, `build_facets`). The federated wrapper
  `bootstrap_store` catches and returns `"error: <msg>"` without propagating
  (`bootstrap.py:95-115`), so other indices still bootstrap — but snsf is left
  unbuilt. The narrower failures I reproduced (persons UPDATE / `grant_persons`
  null) localize to the persons→facets path specifically; the broader
  `INSERT … BY NAME` risk would localize to whichever FK table has an off-type
  column.
- Is the live `data/index/snsf/duckdb/snsf.duckdb` (currently 0 grants / 0
  persons, pre-v3 INTEGER PK) the same DB that failed, or did the failure occur
  on a populated dump? The error is data-dependent, so this matters.
