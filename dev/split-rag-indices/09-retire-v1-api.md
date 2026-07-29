# Task 09 — Retire the v1 API and legacy pipeline

**Severity:** P1 · **Status: DONE 2026-07-15** (commit `a8d465a` on
`feat/split-rag-indices` — suite 1502 green, live smoke: `/v1/*` 404,
extraction works; shared modules moved into v2 homes, see the commit
message) · **Repository:** parent

## Objective

Remove the frozen v1 extraction pipeline and every `/v1/*` surface so the
`3.0.0` release ships one modern API (`/v2`) and one migration event for
consumers. This is the stated product direction: abandon deprecated GME API
versions and make the codebase easier to develop.

## Current v1 surface (verified 2026-07-14)

- `src/v1/` — frozen legacy pipeline (own cache, own parsers; AGENTS.md
  already declares "no new work targets v1").
- `src/api.py` mounts the v1 router; `/v1/extract`, `/v1/repository/*`,
  `/v1/cache/{stats,cleanup,clear,enable,disable}` are all still served and
  bearer-guarded.
- `tests/v1/` (78 tests; 1 is already environment-dependent on the optional
  in-process `gimie` package).
- `justfile`: `cache-stats`, `cache-cleanup`, `cache-clear`, `cache-enable`,
  `cache-disable`, `api-test-extract`, `api-test-extract-refresh`,
  `api-test-gimie` all target `/v1/*` routes.
- v1-only runtime machinery: `api_cache.db` / `CACHE_DB_PATH`,
  `MAX_CACHE_ENTRIES`, in-process gimie fallback (the gimie **sidecar**
  stays — v2 `gather_context` uses it).
- Docs: "Legacy / Historical" mkdocs section (AGENT_STRATEGY,
  INFOSCIENCE_* findings, JSONLD_* guides), `docs/migration-v1-to-v2.md`,
  README quickstart snippets that show `/v1` calls.

## Implementation steps

1. Announce the removal in `CHANGELOG.md` under the same `3.0.0` breaking
   block as the repo split.
2. Delete `src/v1/` and the v1 router mount + v1-specific wiring in
   `src/api.py` (request-logging middleware and startup code that exists
   only for v1 paths goes too).
3. Delete `tests/v1/` and any v2 tests that only assert v1 compatibility
   shims.
4. Remove the v1 justfile recipes and the v1 rows from AGENTS.md
   (auth table, API surface, env vars) — auth simplifies to `/v2/extract` +
   `/v2/jobs/*`.
5. Remove v1-only env vars/config (`CACHE_DB_PATH`, `MAX_CACHE_ENTRIES`,
   v1 cache endpoints) from `.env.example`, Dockerfile, compose, and docs.
6. Decide the fate of `docs/migration-v1-to-v2.md` (keep one release as a
   pointer, or move under a clearly historical section) and prune the
   "Legacy / Historical" nav to pages that still describe current behavior.
7. Grep-guards: no `src.v1`, no `/v1/` route strings outside CHANGELOG and
   clearly historical docs.
8. Run the full remaining suite; update the AGENTS.md "code map".

## Acceptance criteria

- `GET /v1/...` returns 404 from a fresh deployment; `/`, `/docs`,
  `/v2/health` unchanged.
- No `src/v1` directory, no v1 tests, no v1 recipes, no v1 env vars.
- CHANGELOG documents the removal with the migration pointer.
- Full test suite green; image builds and serves `/v2` only.

## Risks

- External consumers may still call `/v1` — confirm with the deployment's
  access logs before the final cut, and coordinate the announcement.
- The in-process gimie fallback removal must not break the v2 sidecar path
  (`GIMIE_API_URL`); keep the sidecar contract tests.
