# RAG/index repository split — implementation task index

Prepared from the 2026-07-14 audit of:

- Parent: `git-metadata-extractor`, branch `feat/split-rag-indices`,
  HEAD `7ca2909`.
- Mounted child: `open-pulse-sources/`, branch
  `feat/migrate-rag-indices`, HEAD `04b5253`.
- Child remote `origin/main`: `7736b9a` (README-only initial commit).

These briefs are intended to be handed to independent subagents. Each task is
self-contained, names both repositories where relevant, and includes explicit
acceptance checks. Do not use the remote child `main` as evidence that the
migration source is missing: the complete implementation is mounted at
`open-pulse-sources/` but has not been pushed.

## Corrected audit verdict

The code transfer is structurally strong, but the split is not release-ready.
The mounted child contains the package, service, index implementation, tests,
scripts, docs, seeds, Docker image, and compose stack. Inventory comparison
found the moved source/config/test surface substantially complete.

Release blockers:

1. ~~Neither feature branch is published; child remote `main` still has only
   its initial README.~~ **Child published 2026-07-14** to
   `sdsc-ordes/open-pulse-sources` (main + `v0.1.0`, CI green, public GHCR
   image; parent defaults pinned to it). The **parent** split branch remains
   unpublished — gated on Task 09.
2. ~~The child wheel omits runtime `.sql` schemas, so non-editable installs
   used by Docker cannot bootstrap DuckDB stores.~~ **Fixed 2026-07-15** in
   child `b98b85d` (released `v0.1.1`, with a CI guard). *The related
   data-root problem is only half-fixed: both compose services now set
   `INDEX_DATA_DIR`, but the child's package-relative fallback still points at
   `site-packages/data` when it is unset — see Task 01.*
3. ~~Parent clean installs/CI do not declare or install the child
   dependency.~~ **Fixed 2026-07-29**: declared and tag-pinned in
   `pyproject.toml` as the single source of truth, with a drift guard —
   see Task 02's "Progress (2026-07-29)".
4. ~~*(Added 2026-07-14)* The v1 API is still served and must be retired in
   the same `3.0.0` breaking release — Task 09.~~ **Done 2026-07-15**
   (`a8d465a`).

**Remaining release blocker (2026-07-29):** no published child tag contains
the service-side 503 fix on child `main` — cut `v0.1.2` and re-pin, then
publish the parent branch. Detail in Task 02.

Important follow-ups:

- No child CI exists and its quality recipes still point at deleted `src/`.
- Parent library, child service image, Qdrant, and Selenium use mutable refs or
  `latest`.
- Parent and child can both write shared DuckDB/Qdrant state; process-local
  locks do not enforce a cross-service single-writer rule.
- Snapshot adoption is incomplete for parent DuckDB readers.
- The split migration script has stale root/path assumptions and does not fail
  reliably.
- One parent HuggingFace provider imports a child module that does not exist and
  silently disables itself.
- Config is duplicated and CWD-relative.
- Active docs and recipes retain old module names, paths, and route ownership.

## Verified strengths

- Child contains the copied index/module/service implementation and retained
  tests.
- Parent removed `src/index`, `src/module`, `src/v2/indices`, and the local
  management routes.
- Child service preserves `/v2/manifest` and `/v2/indices/*`.
- Management routes use fail-closed bearer auth.
- Request models enforce limits and reject extra fields.
- Dedicated ingest pool and atomic read-only snapshot machinery exist.
- Both repos have deployment wiring for a separate sources service.
- The 32 `config/index/*.yaml` files currently match.

## Task order

| Order | Task | Severity | Parallelism |
|---|---|---|---|
| 1 | [01 — Package runtime assets](01-package-runtime-assets.md) | ~~P0~~ **packaging DONE 2026-07-15** (child `v0.1.1`) — unsafe `site-packages` data-root fallback remains | — |
| 2 | [02 — Cross-repo dependency and release](02-cross-repo-dependency-release.md) | ~~P0~~ **parent dependency contract DONE 2026-07-29** — child `v0.1.2` tag + parent branch publication remain | Publish gated on the `v0.1.2` go/no-go |
| 3 | [03 — Child CI and quality gates](03-child-ci-quality-gates.md) | ~~P1~~ **DONE 2026-07-15** (MyPy + branch protection remain) | — |
| 4 | [04 — Cross-repo contract tests](04-cross-repo-contract-tests.md) | **core DONE 2026-07-15** (import-contract test, HF fix, 503 mapping; fixtures/CI-matrix remain) | — |
| 5 | [05 — Single-writer operation boundary](05-single-writer-operation-boundary.md) | P1 | Parallel design task |
| 6 | [06 — Snapshot and read-path completion](06-snapshot-read-path-completion.md) | P1 | Coordinate with 05 |
| 7 | [07 — Migration utility hardening](07-migration-utility-hardening.md) | P1 | Parallel |
| 8 | [08 — Config ownership and docs cleanup](08-config-and-docs-cleanup.md) | P2 | Parallel |
| 9 | [09 — Retire the v1 API](09-retire-v1-api.md) | ~~P1~~ **DONE 2026-07-15** | was gating the 3.0.0 publish — unblocked |
| 10 | [10 — Developer-experience modernization](10-dev-experience.md) | P2 | Parallel; best after 09 |
| 11 | [11 — GIMIE sidecar JSON-LD broken](11-gimie-sidecar-jsonld-broken.md) | ~~P0~~ **fix SHIPPED 2026-07-14** (`8a45491`) — parity-script + route-contract check remain | — |

Tasks 09–10 were added 2026-07-14 to carry the product direction for this
release: `3.0.0` abandons the deprecated v1 API in the same breaking
release as the repo split, and both repos get modern, uniform developer
ergonomics.

## Compose validation run (2026-07-14)

Both service images were built locally and exercised with docker compose
(child branch is unpushed, so the parent test image installed the child
**wheel built from the local clone** via a scratch Dockerfile; the repo's
Dockerfile still targets git and is untouched). Isolated project name
`gme-smoke`, throwaway volumes, dummy `API_TOKEN`, mock providers — no
external APIs or real credentials. Stack torn down afterwards; images
`gme-api:split-test` / `open-pulse-sources:split-test` kept locally.

Child standalone stack (`ops-sources` + `ops-qdrant`):

| Check | Result |
|---|---|
| `GET /health` (open) | 200 |
| `GET /v2/manifest` without token | 401 (fail-closed) ✓ |
| `GET /v2/manifest` with token | 200, 25 stores ✓ |
| `GET /v2/indices/github_repos/stats` (empty volume) | 503 with clean detail ✓ |
| unknown provider stats / unknown job id | 404 / 404 ✓ |
| `POST …/zenodo_records/search` with no `RCP_TOKEN` | **500 raw** (finding → Task 04) |
| OpenAPI | 67 paths ✓ |

Combined stack (`gme-api` + `gme-sources` + `gme-gimie-api` + `gme-qdrant`):

| Check | Result |
|---|---|
| gme-api `GET /v2/health` | 200 |
| gme-api `GET /v2/manifest` / `/v2/indices/*` | 404 — routes really moved ✓ |
| gme-api `GET /v1/cache/stats` | **200 — v1 still served** (→ Task 09) |
| gme-sources `GET /v2/manifest` | 200, 25 stores ✓ |
| e2e `POST /v2/extract` (mock, rule_based, octocat/Hello-World) | job → `completed`; graph = SoftwareSourceCode + Person + Contribution ✓ |
| gme-api startup bootstrap | **31/31 stores not ready** — 5× missing wheel `schema.sql` (Task 01), 26× `Permission denied: site-packages/data` (missing `INDEX_DATA_DIR` + unsafe package-relative fallback; Tasks 01/08) |

Incidental: mock providers need `tests/v2/fixtures/` (not shipped in the
image — bind-mounted for the test; → Task 10), and the build was only
possible after extending the parent root `.dockerignore` (36 GB `data/`
was in the build context; → Task 08, uncommitted working-tree fix).

## Evidence and commands already run

- `git diff --stat origin/develop...HEAD` in parent:
  `798 files changed, 281 insertions, 78902 deletions`.
- Parent clean declared-dependency import:
  `uv run --isolated --extra dev python -c "import src.api"` failed with
  `ModuleNotFoundError: open_pulse_sources`.
- Parent DOI contract test failed during collection for the same reason.
- Child local history:
  `378827b`, `0353e3c`, `173e8d3`, `e457ccb`, `04b5253`.
- Child remote tree inspection showed only `README.md`; this is a publication
  gap, not a missing local implementation.
- `git diff --check origin/develop...HEAD` in parent reports one extra blank
  line at EOF in `src/v2/api_models/contracts.py`.
- The Canvas report is stored outside the repository at
  `C:\Users\Kato\.cursor\projects\d-pro-git-metadata-extractor\canvases\rag-index-split-audit.canvas.tsx`.

## Rules for delegated agents

- Treat `open-pulse-sources/` as a separate Git repository.
- Do not commit or push unless explicitly requested.
- Do not modify `.env` or print credentials.
- Keep changes in the repository that owns the behavior.
- Run the focused checks in each brief, then the repository's full applicable
  quality gates.
- Report files changed, behavior change, commands/results, and remaining risk.

