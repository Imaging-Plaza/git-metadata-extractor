# Task 08 — Config ownership, stale recipes, and documentation cleanup

**Severity:** P2 · **Status:** Ready · **Repositories:** both

## Objective

Define one durable configuration contract and remove stale monolith paths,
commands, endpoint ownership, and package names from active documentation.

## Config findings

- Both repositories currently contain 32 byte-identical
  `config/index/*.yaml` files.
- Child config loaders remain CWD-relative.
- Config YAML is not packaged with the installed child library.
- Parent image copies its own config tree; child image must independently ship
  a compatible tree.
- `config/index/epfl_graph.yaml` defaults Qdrant to
  `http://localhost:6333`, unlike the service network default, unless
  `INDEX_QDRANT_URL` overrides it.
- Duplication works today but has no automated drift guard.
- **(2026-07-14)** Neither compose file sets `INDEX_DATA_DIR`, so index
  stores fall back to package-relative root resolution — under a wheel
  install that is `site-packages/data` (26 stores failed bootstrap with
  `Permission denied` in the live smoke run). Both `gme-api` and
  `gme-sources`/`ops-sources` service definitions must set
  `INDEX_DATA_DIR=/app/data/index` explicitly; see Task 01 for the
  fallback fix itself.

## Documentation/recipe findings

Child:

- `justfile` retains stale GitHub/Zenodo recipes targeting nonexistent
  `open_pulse_sources.index.github` and `.zenodo` packages.
- Quality and reload recipes target deleted `src/` paths.
- Some docs retain `from src.index...` and old module names.
- ~~`MIGRATION.md` contains obsolete "did not move yet" narrative alongside the~~
  ~~completed phase record; preserve history but label superseded sections.~~
  **Resolved differently (2026-07-14): the maintainer removed `MIGRATION.md`**;
  the split record now lives in the parent repo (CHANGELOG + these briefs).

Parent:

- `README.md` still lists `src/index/`.
- `docs/getting-started.md`, `docs/api-and-cli.md`, and architecture docs still
  show removed `just hf-*`, `gme-*`, and `python -m src.index.*` commands.
- `docs/v2-rag-tools.md` says providers are backed by local `src/index/*`.
- `.env.example` and YAML headers refer to removed local paths/routes.
- `Makefile` runs `python -m src.index._federated.bootstrap`.
- `src/v2/rate_limit.py` still special-cases moved `/v2/indices/*/ingest`
  routes on the parent service.
- `pyproject.toml` retains a marker describing `src/index/openalex`.
- Parent Docker comments say the deleted migration script is copied locally.
- `AGENTS.md` references child-only `just epfl-graph-*` recipes as if local.
- `git diff --check` reports an extra blank line at EOF in
  `src/v2/api_models/contracts.py`.

## Build-context findings (added 2026-07-14)

- The parent root `.dockerignore` excluded none of the heavy local state:
  `data/` (~36 GB), `.venv` (~800 MB), `.venv-win`, `logs/`, and the
  nested `open-pulse-sources/` clone (with its own ~530 MB venv). Any
  local `docker build` from the repo root was effectively impossible.
  A working-tree fix now exists (uncommitted) — fold it into this task.
- `tools/image/.dockerignore` is **dead configuration**: BuildKit only
  reads `<Dockerfile-path>.dockerignore` (i.e.
  `tools/image/Dockerfile.dockerignore`) or the context root's
  `.dockerignore`. Delete it or rename it to the effective name.
- The child repo had no `.dockerignore` at all (its `.venv` went into
  the build context). A working-tree file now exists — review and keep.

## Configuration decision options

Choose one:

1. Child package resources with an explicit `config_root` override.
2. Child service owns config; parent uses HTTP and no longer loads child YAML.
3. Deliberate duplication with a generated sync/checksum contract in CI.

Avoid relying on arbitrary process CWD as the primary contract.

**Recommendation (2026-07-14): option 1.** It removes the CWD-relative
fragility both other options merely work around, and it composes with
Task 01 (the yaml files ride the same package-data mechanism as the SQL
schemas). Option 3 is an acceptable stopgap if option 1 slips.

**Seeds decision (implemented 2026-07-14, uncommitted):** the top-level
`seeds/` moved to **`config/seeds/`** — seeds are curated, deployment-
specific parameters (config), not disposable runtime state (`data/` is
gitignored and volume-shadowed in Docker, so seeds there would vanish).
`scripts/reingest_indices.py` now honors `--seeds-dir` / `$SEEDS_DIR`
(and its root-resolution had the same stale `parents[2]` depth bug as
Task 07's script — fixed to `parents[1]`). The child Dockerfile's
separate `COPY seeds` dropped (rides along with `COPY config`).
Remaining for this task: fold the txt lists into the per-index yaml
`scope.seeds` blocks (github_repos/huggingface already do this) so one
seed mechanism remains, and ship them as package resources per option 1.

## Implementation steps

1. Decide and document config ownership and precedence.
2. Add explicit config-root handling or a drift check.
3. Normalize network-safe defaults; retain environment overrides.
4. Update child recipes to real package paths/modules.
5. Sweep active parent and child docs for:
   - `src/index`,
   - `src/module`,
   - removed `just` recipes,
   - old `python -m` module paths,
   - moved `/v2/indices` route origin,
   - stale migration locations.
6. State the child service base URL/port, not only unchanged path suffixes.
7. Separate historical design records from current operator instructions.
8. Remove stale parent rate-limit/Makefile infrastructure or redirect it to the
   child service intentionally.
9. Correct minor diff formatting.
10. Add doc/config grep guards to prevent regression.

## Acceptance criteria

- A developer can follow both READMEs from clean checkout without invoking a
  removed command.
- Every active module path and recipe exists.
- Management routes clearly identify the sources service origin.
- Config resolution works independently of launch CWD.
- Config trees cannot drift silently if duplication remains.
- `git diff --check` is clean.

