# Task 01 — Package runtime assets in `open-pulse-sources`

**Severity:** P0 · **Status: packaging DONE 2026-07-15** (child commit
`b98b85d`, released as `v0.1.1`: `.sql` schemas ship in wheels + a CI guard).
**Remaining:** the scope-amendment item (b) — 13 child `paths.py` modules
still fall back to `Path(__file__).resolve().parents[3]`, i.e.
`site-packages/data`, when `INDEX_DATA_DIR` is unset. Both compose services
now set it explicitly (item (a) done), so the unsafe fallback is masked, not
fixed; it must fail loudly or resolve CWD-relative instead. ·
**Repository:** `open-pulse-sources/`

## Objective

Make a built, non-editable `open-pulse-sources` wheel contain every runtime
resource required by the index stores and service, especially SQL schemas.

## Confirmed problem

`open-pulse-sources/pyproject.toml:66-67` discovers Python packages but does not
declare package data. The generated
`open_pulse_sources.egg-info/SOURCES.txt` contains no `.sql` files, while store
implementations load schema files by path at runtime. Example:

- `open_pulse_sources/index/github_repos/storage/duckdb_store.py:23-29`
- Equivalent schema loaders exist across the index families.

Both Docker images use non-editable installs, so editable-checkout success does
not prove production correctness. The audit counted 26 runtime SQL schemas at
risk.

**Now also blocking the parent test suite (2026-07-14, after the local
clone was deleted):** with the library installed non-editable from the git
tag, 15 parent tests fail on the missing wheel `schema.sql`
(`tests/v2/test_agent_tool_snsf_grants.py` — 13, and
`tests/v2/test_disciplines_duckdb_concurrency.py` — 2). Fixing this task
in the child (+ a `v0.1.1` tag, then bumping the parent pins) restores a
green parent suite.

**Empirically confirmed (2026-07-14 compose validation):**

- `uv build --wheel` produced `open_pulse_sources-0.1.0-py3-none-any.whl`
  with 590 files, **0 `.sql` and 0 `.yaml`**.
- A parent image built from that wheel logged at gunicorn startup:
  `index bootstrap on start: 31 store(s) not ready`, including
  `No such file or directory: .../site-packages/open_pulse_sources/index/
  <infoscience|ror|snsf|zenodo_communities|ethz_research_collection>/storage/schema.sql`.
- The child's own image only works **by accident**: it `COPY`s the source
  tree and sets `PYTHONPATH=/app`, which shadows the wheel-installed
  package (verified from a live traceback path `/app/open_pulse_sources/…`).

**Scope amendment — data-root resolution:** the same bootstrap run showed
the other 26 stores failing earlier with
`Permission denied: '/usr/local/lib/python3.12/site-packages/data'`.
Root cause: `paths.py` in 13 modules falls back to
`Path(__file__).resolve().parents[3]` as the project root when
`INDEX_DATA_DIR` is unset — under a wheel install that is `site-packages`.
The env override exists (39 files honor `INDEX_DATA_DIR`) but neither
compose file sets it. Fix both here: (a) deployment sets
`INDEX_DATA_DIR=/app/data/index` explicitly (see Task 08), and (b) the
package-relative fallback must fail loudly or default to CWD — never
site-packages. The Task-01 acceptance test must run the wheel-install
bootstrap **with `INDEX_DATA_DIR` set** so the two failure classes are
verified independently.

## Implementation steps

1. Inventory all non-Python runtime assets under `open_pulse_sources/`:
   - `*.sql`
   - JSON/YAML/templates or other files opened relative to `__file__`
   - ontology/static resources, if any
2. Add explicit setuptools package-data configuration in `pyproject.toml`.
   Prefer scoped patterns broad enough to cover every index package without
   shipping caches, DuckDB data, or secrets.
3. Build both sdist and wheel in a clean environment.
4. Inspect archive contents and assert every inventoried runtime file is
   present.
5. Install the wheel into an isolated environment outside the checkout.
6. Run index bootstrap/store construction against a temporary data directory.
7. Add a regression test that fails if required package resources disappear
   from future wheels.
8. Verify the sources Docker image can start and bootstrap from the installed
   wheel rather than relying on source-tree files.

## Suggested tests

- Parametrize all store schema loaders and assert their paths exist after wheel
  installation.
- Build-wheel test that compares expected `*.sql` paths with wheel members.
- Smoke `open_pulse_sources.index._federated.bootstrap.bootstrap_all()` using a
  temporary `INDEX_DATA_DIR`.
- Service startup/import smoke from the installed wheel.

## Acceptance criteria

- Wheel and sdist contain all required runtime assets.
- No store schema load depends on the repository checkout.
- Bootstrap succeeds from an isolated wheel installation.
- Docker service starts with the built artifact.
- Tests document the package-data contract.

## Risks

- Avoid accidentally packaging real `data/index` stores or credentials.
- A broad recursive pattern may bloat the wheel; verify archive contents.
- Schema loaders using CWD-relative paths are a separate config-ownership issue
  covered by Task 08.

