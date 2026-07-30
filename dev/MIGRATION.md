# Migration: git-metadata-extractor → open-pulse-sources

**Status: COMPLETE — all four phases done (2026-07-02).**
This repo: 593 tests green. Monolith (branch `feat/split-rag-indices`):
1487 green, index layer removed, consumes this repo as the
`open_pulse_sources` library. Deployment split in both compose stacks.

This repo takes over the **RAG source indices** half of the
`git-metadata-extractor` monolith. The extraction service (v1/v2 pipeline)
stays behind; this repo owns everything that *builds and refreshes* the
indices the extractor searches.

- Source of the copy: `git-metadata-extractor` @ `64f1d14`
  (branch `docs/ontology-v3-reference`, 2026-06-13 — main + one docs commit).
- Copy date: 2026-07-02.
- History was **not** carried over (plain copy, not `git filter-repo`).
  Full history remains in the monolith.

## The seam

Runtime coupling between the two halves is intentionally thin and stays
unchanged for now:

- **Extractor reads, sources write.** The v2 extraction pipeline's RAG
  tools (`src/v2/ingest/providers/*_rag.py`, agent tools) query **Qdrant
  directly** (+ RCP for query embedding/rerank) and, in a few places, read
  the per-index **DuckDB** files under `data/index/<name>/`.
- **This repo owns the write side**: ingest CLIs, DuckDB stores, embed
  pipelines, Qdrant collection management, per-index FastAPI apps, and the
  federated cross-index layer.
- **Shared contract** = Qdrant collection names + payload shapes, the
  `data/index/<name>/duckdb/` paths, and the RCP embed/rerank endpoints.
  As long as both services see the same Qdrant instance and (for the
  DuckDB-reading providers) a shared `data/index/` volume, nothing breaks.

## What was copied (Phase 1 — done)

| New repo path | Origin in monolith | Notes |
|---|---|---|
| `src/index/` | `src/index/` | all ~40 indices + `_federated`, `_rcp`, `_shared`, `_snapshot`, base packages |
| `src/module/` | `src/module/` | `dependents` scraper + `epfl_graph` (graphai client). ⚠ monolith also keeps its copy — `v2` concept_tagging uses it |
| `src/common/canonicalization/` | `src/v2/canonicalization/` | pure IRI builders + string utils |
| `src/common/cache.py` | `src/v2/ingest/cache.py` | SQLite ProviderCache |
| `src/common/detection/` | `src/v2/ingest/detection/` | GitHub URL classifier |
| `src/common/providers/{base,orcid_provider,rate_limiter,orcid_oauth}.py` | `src/v2/ingest/providers/…` | ORCID ingest stack |
| `src/common/query_log.py` | `src/v2/observation/query_log.py` | |
| `src/common/selenium_fetch.py` | `src/v2/agents/llm/agent_tools/selenium_fetch.py` | used by `module/dependents` |
| `tests/index/` | `tests/index/` | there was no conftest.py to carry |
| `config/index/` | `config/index/` | |
| `seeds/` | `seeds/` | |
| `docs/` | index-related pages from `docs/` | see list in repo |
| `justfile` | header rewritten; index recipe sections (Infoscience → Federated) verbatim | ⚠ inherits stale recipes: `gh-*`/`zenodo-*` still call `open_pulse_sources.index.github` / `open_pulse_sources.index.zenodo`, renamed upstream to `github_repos` / `zenodo_records` |
| `pyproject.toml` | new; dep pins mirror monolith | dep set derived from actual imports of `src/index` + `src/module` |

**Import rewrites applied** across the copied tree (verified zero
`src.v1`/`src.v2` references remain):

```
src.v2.canonicalization              → open_pulse_sources.common.canonicalization
src.v2.ingest.cache                  → open_pulse_sources.common.cache
src.v2.ingest.detection              → open_pulse_sources.common.detection
src.v2.ingest.providers.base         → open_pulse_sources.common.providers.base
src.v2.ingest.providers.orcid_provider → open_pulse_sources.common.providers.orcid_provider
src.v2.ingest.providers.rate_limiter → open_pulse_sources.common.providers.rate_limiter
src.v2.ingest.providers.orcid_oauth  → open_pulse_sources.common.providers.orcid_oauth
src.v2.observation.query_log         → open_pulse_sources.common.query_log
src.v2.agents.llm.agent_tools.selenium_fetch → open_pulse_sources.common.selenium_fetch
```

The `src/common/*` modules are **copies, not a shared package** — the
monolith keeps its originals. They are small and stable (pure functions,
one SQLite cache); divergence risk is acceptable until Phase 3 decides a
final home.

## Phase 2 — service API (done 2026-07-02)

The index management surface is now a standalone FastAPI service here:

| New repo path | Origin in monolith | Notes |
|---|---|---|
| `src/service/indices/` | `src/v2/indices/` | ingest/search runners, jobs store, stats, compact, reset — imports rewritten to `open_pulse_sources.service.*` / `open_pulse_sources.common.cache` |
| `src/service/api.py` | `src/v2/api.py` lines 2554–4338 | the `/v2/manifest` + `/v2/indices/*` block, verbatim; plus ported helpers (`_resolve_provider_cache`, `_track_background_task`) |
| `src/service/api_models.py` | `src/v2/api_models/contracts.py` lines 197–596 | index-related models only (self-contained) |
| `src/service/auth.py` | `src/v2/auth.py` | unchanged (env `API_TOKEN`, fail-closed) |
| `src/service/app.py` | new | FastAPI entrypoint; `/health` open; router keeps the **`/v2` prefix** so consumers can repoint without client changes |
| `tests/service/` | 19 files from `tests/v2/` | `test_indices_*`, `test_index_*`, `test_api_snsf_grants`, `test_embed_step_snapshot`, `test_ingest_pool`, `test_dockerhub_index`, `test_huggingface_models_lineage` + minimal conftest (API_TOKEN autouse fixture mirroring the monolith's) |

Environment contract unchanged: `API_TOKEN`, `V2_PROVIDER_CACHE_PATH` /
`_TTL_DAYS` / `_ENABLED` keep their names (rename deferred; decide before
1.0). Serve with `just serve` (uvicorn `open_pulse_sources.service.app:app`).

Port fixes worth knowing:

- **fastapi pinned to `0.136.3`** (monolith's locked version) — 0.139's
  lazy `include_router` breaks route introspection in the ported tests.
- `tests/service/test_indices_stats_endpoint.py` fixture now inserts
  aware datetimes as UTC-naive; the original wrote aware values into a
  naive TIMESTAMP column, which DuckDB localises to the session timezone
  — the test only passed on UTC machines (devcontainer/CI).

## What did NOT move yet

- **Extract-side auto-ingest** (`src/v2/api.py` ~lines 1850–2130 +
  `test_github_auto_ingest_client_signature.py`): after an extraction the
  monolith directly invokes `open_pulse_sources.index.*` ingest for github_repos /
  github_users / github_organizations / huggingface_papers
  (`V2_*_RAG_AUTO_INGEST` flags). Phase 3 must rewire this to POST to
  this service's `/v2/indices/<name>/ingest` (new env:
  `SOURCES_SERVICE_URL` + token) — or drop the feature.
- **RAG-tool read-side tests** (`test_llm_*_rag_tool.py`, etc.) — stay
  with the monolith permanently.
- **Deployment** — compose files / Dockerfile under `tools/` still build
  the monolith image only. Phase 4.
- **Read-side RAG tools** stay in the monolith permanently (they are the
  extractor's client code). At Phase 3 the monolith must vendor its own
  tiny copies of what it currently imports from `open_pulse_sources.index`:
  `_rcp` embed/rerank clients, `openalex.vector.qdrant_store.QdrantStore`,
  `_shared.doi`, collection-name constants, and the DuckDB readers used by
  `zenodo_rag` / `snsf` facet query / `epfl_graph` disciplines stage.

## Package rename (Phase 3 enabler, 2026-07-02)

The top-level package was renamed **`src` → `open_pulse_sources`**
(`from open_pulse_sources.index... import ...`). Reason: Phase 3 makes the
monolith consume this repo **as an installed library** (its read-side RAG
providers import the retrieval layer of nearly every index — vendoring
those ~50 modules would have created a permanent semi-fork), and two
packages both rooted at `src` cannot be installed into one environment.
Path tables earlier in this file predate the rename: `src/…` in a
"new repo path" column now reads `open_pulse_sources/…` on disk.

## Phase plan

1. **✅ Phase 1 — stand up this repo.** Copy write-side code, rewrite
   imports, scaffold packaging/tests/docs. Monolith untouched; both repos
   fully functional independently (this one pending first `uv sync` + test
   run on a machine with the deps).
2. **✅ Phase 2 — service API.** Ported `src/v2/indices/*` + the index
   endpoints from `src/v2/api.py` into `src/service/` (standalone FastAPI
   app, 67 routes) with the matching 19 test files. Suite: 586 green.
3. **✅ Phase 3 — cutover (done 2026-07-02).** Executed as a **library
   dependency, not vendoring** (see "Package rename" above): the monolith
   deleted `src/index`, `src/module`, `src/v2/indices`, the
   `/v2/manifest` + `/v2/indices/*` routes, `tests/index`, 21 `tests/v2`
   index tests, `seeds/`, index docs/justfile recipes and index ops
   scripts (all absorbed here), and its remaining read-side imports were
   rewritten `src.index.*`/`src.module.*` → `open_pulse_sources.*`.
   `just install-dev` there installs this repo editable from a sibling
   clone (or from git). Extract-side **auto-ingest kept as-is** — it now
   writes through this library into the same `data/index` + Qdrant
   stores (the POST-to-service rewire remains an option later).
   Monolith branch: `feat/split-rag-indices`; suite 1487 green.
   `config/index/*.yaml` intentionally stays duplicated in the monolith
   (the library resolves config/data CWD-relative).
4. **✅ Phase 4 — deployment split (done 2026-07-02).**
   - This repo: `tools/image/Dockerfile` (uvicorn service on :8080) +
     `tools/deploy/docker-compose.yml` (standalone: ops-sources +
     ops-qdrant + ops-selenium) + justfile docker recipes.
   - Monolith: its image now pip-installs this library from git
     (`--build-arg OPEN_PULSE_SOURCES_REF=<tag|sha>` to pin — note the
     gunicorn `on_starting` index bootstrap imports it too); its compose
     gained a `gme-sources` service sharing `gme-data` (DuckDB) and
     `gme-qdrant` with `gme-api`.
   - ⚠ **Operational note:** DuckDB's file lock is per-process. With both
     services on one volume, keep heavy ingest on `gme-sources` and
     consider `V2_*_RAG_AUTO_INGEST=false` on `gme-api` so two writers
     don't contend for the same store file.

## Follow-ups (post-split backlog)

- Publish this package (tag + GitHub release, or PyPI) and pin the
  monolith's dependency to a version instead of `main`.
- Set up CI in this repo (`pytest`, `scripts/check_import_closure.py`).
- Consider moving the extractor's read side to HTTP (this service's
  `/v2/indices/*/search`) to drop the shared-volume requirement.
- Decide `src/common` divergence policy (the monolith kept its originals
  of canonicalization/cache/detection/ORCID stack).
- Fix the stale `gh-*`/`zenodo-*` justfile recipes (renamed modules).
- Rename the `V2_*` env vars this service inherited, before 1.0.

## Verification status (2026-07-02)

- `grep` sweep: zero `src.v1` / `src.v2` imports in the copied tree; all
  imports resolve within `open_pulse_sources.index` / `open_pulse_sources.module` / `open_pulse_sources.common`.
- `python scripts/check_import_closure.py`: 660 files, all `src.*`
  imports resolve inside this repo; whole tree byte-compiles.
- `pytest tests/ -m 'not live_provider and not llm_integration'`:
  **390 passed** (Python 3.12, fresh `uv` venv). One test fix was needed:
  `tests/index/orcid/test_scope.py` loaded fixtures from the monolith's
  `tests/v2/fixtures/providers/orcid/`; those 4 JSONs now live in
  `tests/index/orcid/fixtures/` and the path is `__file__`-relative.

## Re-syncing before cutover

Until Phase 3 removes the code from the monolith, the monolith remains
the source of truth for `src/index`. If it changes, re-copy the changed
files and re-apply the import rewrites (the sed table above), or
cherry-pick manually. Diff base: monolith `64f1d14`.
