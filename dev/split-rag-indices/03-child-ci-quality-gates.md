# Task 03 — Child CI and quality gates

**Severity:** P1 · **Status:** Ready · **Repository:** `open-pulse-sources/`

## Objective

Add automated enforcement for the child package, service, tests, import
closure, image build, and package resources.

## Confirmed problem

The mounted child has substantial test coverage but no tracked
`.github/workflows` files. `MIGRATION.md:169-174` listed CI as follow-up
(the maintainer removed `MIGRATION.md` from the child on 2026-07-14; its
citations in these briefs are historical).

**Partial seed exists (2026-07-14, uncommitted):**
`open-pulse-sources/.github/workflows/ci.yml` now covers: locked
`uv sync --frozen` install, the non-live pytest suite, the import-closure
guard, and an image job (build → in-container smoke of `/health` +
fail-closed auth + authenticated `/v2/manifest` → publish to
`ghcr.io/<owner>/open-pulse-sources` with `latest` from the default
branch, branch tags, semver tags, and `sha-*` tags for pinning).
Remaining scope for this task: Ruff/format + MyPy gates (the codebase
carries ~2,034 inherited ruff findings today — burn down or baseline
before gating), a Python-version matrix, wheel/package-data drift checks
(Task 01), branch protection, and the justfile path corrections below.

Existing quality recipes target the removed pre-rename tree:

- `justfile:33-34` uses `--reload-dir src`.
- `justfile:52-62` runs Ruff/MyPy against `src/`.

The real package is `open_pulse_sources/`, so those commands can pass while
checking nothing relevant. `uv.lock` exists, but Docker and recipes use
unfrozen dependency resolution rather than `uv sync --frozen`.

## Implementation steps

1. Correct every recipe path from deleted `src/` to
   `open_pulse_sources/` where appropriate.
2. Define canonical local commands for:
   - full non-live pytest suite,
   - Ruff check and format check,
   - MyPy,
   - `scripts/check_import_closure.py`,
   - wheel/sdist build and package-data validation,
   - service import/start smoke,
   - Docker build smoke.
3. Decide whether CI installs from `uv.lock`; use a frozen mode where supported.
4. Add CI with a supported Python-version matrix.
5. Cache dependencies without caching built project artifacts that could hide
   missing package data.
6. Run unit/service tests without external credentials.
7. Keep live-provider and LLM integration tests opt-in or scheduled.
8. Add a Docker build job and minimal health check.
9. Add generated/import/package-data drift checks.
10. Require CI before merging into the child default branch.

## Suggested workflow jobs

- `quality`: Ruff format/check, MyPy, import closure.
- `tests`: Python matrix, non-live tests with xdist where stable.
- `package`: build archives, inspect resources, isolated install/import.
- `service-image`: Docker build, run with mock/minimal config, `/health`.
- `integration`: optional parent/child compatibility job from Task 04.

## Acceptance criteria

- CI runs on pull requests and default-branch pushes.
- No quality command references nonexistent `src/`.
- Dependency installation is reproducible/frozen.
- Package-data omission from Task 01 is caught automatically.
- The service image builds and reaches its open health endpoint.
- Branch protection/rules require the critical jobs.

## Evidence to retain

The (since-removed) `MIGRATION.md` recorded prior local results (593 child tests and 1487 parent
tests), but CI must independently reproduce them. Do not treat recorded counts
as a substitute for a current workflow run.

