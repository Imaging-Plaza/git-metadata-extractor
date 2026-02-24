# Risk Register

Repository-level operational and implementation risks that require explicit awareness.

## Active Risks

### Run failure diagnostics are embedded in untyped JSON stats
- Area: `src/v2/graph/store.py`, `runs` table schema
- Risk: `fail_run()` stores `error_detail` inside the `stats` JSON payload because `runs` has no dedicated `error_detail` column.
- Impact: SQL-level analytics/filtering by failure reason is brittle and depends on JSON parsing conventions.
- Current control:
  - `get_run()` normalizes `error_detail` out of `stats` into the `Run` model for API-facing consumers.
  - v2 run CRUD tests cover failed-run write/read behavior.
- Operator guidance: Add a dedicated nullable `error_detail` column if production reporting needs indexed/typed failure diagnostics.

### In-memory RDF graph can drift in multi-process deployments
- Area: `src/v2/graph/store.py`, `src/v2/graph/rdf_sync.py`
- Risk: RDF deltas are applied in-process on each `GraphStore` instance, while SQLite remains the shared source of truth.
- Impact: Different worker processes can hold temporarily divergent in-memory graphs until reloaded from SQLite.
- Current control:
  - Startup bootstrap reloads RDF state from SQLite for each process.
  - All entity/edge CRUD operations in `GraphStore` apply local RDF deltas immediately.
- Operator guidance: Treat SQLite as canonical for consistency checks and schedule process restarts or reload hooks after external DB maintenance.

### Entity provenance payload growth is unbounded
- Area: `src/v2/graph/provenance.py`, `entities.provenance`
- Risk: Each field-level update appends a full change record to the entity row without retention limits.
- Impact: Large provenance arrays can increase row size, update cost, and API payload overhead over time.
- Current control:
  - Provenance writes are append-only and JSON-serializable.
  - Field-level filtering API is available via `get_provenance_for_field()`.
- Operator guidance: Plan retention/compaction policy before high-volume production ingestion.

### Graph store rollback is destructive when enabled
- Area: `src/v2/graph/migrations.py`
- Risk: `MigrationRunner.rollback_to()` rebuilds graph-store tables to reach a target version.
- Impact: Existing rows in graph-store tables can be removed during rollback.
- Current control:
  - Destructive rollback is blocked by default.
  - It requires explicit opt-in via `allow_destructive_rollback=True` or `V2_GRAPH_ALLOW_DESTRUCTIVE_ROLLBACK=1`.
- Operator guidance: Treat rollback as dev-only unless you have a verified backup and maintenance window.

### Manual backup dependency for SQLite DB
- Area: v2 graph-store operations
- Risk: Recovery depends on external/manual backup procedures for the SQLite file.
- Impact: Accidental destructive operations can become irreversible without a backup.
- Current control: Manual operator backups before migration or rollback operations.

### SQLite write concurrency limitations
- Area: v2 graph-store runtime
- Risk: SQLite allows one writer at a time; concurrent write-heavy workloads can raise lock contention.
- Impact: Elevated write latency or intermittent write failures under load.
- Current control: Not fully addressed yet; planned in `.internal/v2-plan/phase-4-graph-store/P4-11-concurrent-write-safety.md`.
