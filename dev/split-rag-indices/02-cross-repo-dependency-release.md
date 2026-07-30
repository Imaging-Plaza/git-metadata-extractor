# Task 02 — Publish and pin the cross-repository release

**Severity:** P0 · **Status: parent dependency contract DONE 2026-07-29**
(steps 4–9 below; see "Progress (2026-07-29)"). **Remaining:** cut child
`v0.1.2` so the published tag/image includes the service 503 fix, publish the
parent split branch (step 10), and digest-pin production. ·
**Repositories:** both

## Objective

Publish the mounted child implementation and make parent package/image/service
consumption reproducible from one immutable child revision.

## Confirmed problem

- Child local branch `feat/migrate-rag-indices` has implementation commits
  `378827b..04b5253`.
- Child remote `main` is still `7736b9a`, containing only `README.md`.
- Parent branch `feat/split-rag-indices` is also local.
- Parent `pyproject.toml:28-32` documents the child but declares no dependency.
- `just install-dev` installs a nested checkout or unpinned Git default branch.
- Parent Docker defaults `OPEN_PULSE_SOURCES_REF=main`.
- Parent compose defaults `ghcr.io/sdsc-ordes/open-pulse-sources:latest`.
- Sources package version is `0.1.0`; parent is `3.0.0rc1`.

Until the child branch is published, parent Docker builds from the declared
default cannot install the required package.

## Decisions required

1. Distribution mechanism:
   - tagged VCS dependency,
   - private package registry,
   - or public PyPI package.
2. ~~Whether RAG support is mandatory or an install extra.~~
   **Decided (2026-07-14): mandatory.** `src/v2/canonicalization/doi.py`,
   the pipeline stages and the gunicorn bootstrap import it
   unconditionally; an "extra" would be a fiction. Declare it as a hard
   dependency.
3. Compatibility policy between GME and sources versions.
4. Release/tag naming and container-tag/digest policy.

## Sequencing note (added 2026-07-14)

Gate step 10 (publishing the parent split branch / cutting `3.0.0`) on
**Task 09 (retire the v1 API)** as well: the repo split is already a
breaking release, and folding the v1 removal into the same `3.0.0` gives
consumers one migration event instead of two.

## Progress (2026-07-14, later)

- **Child published**: https://github.com/sdsc-ordes/open-pulse-sources
  (`main` + tag `v0.1.0`, note the **sdsc-ordes** org — not caviri). CI
  green on both refs; `ghcr.io/sdsc-ordes/open-pulse-sources` is public
  (`latest`, `main`, `sha-*`, `0.1.0`, `0.1`).
- **Parent defaults pinned to the release** (uncommitted):
  Dockerfile `OPEN_PULSE_SOURCES_REF=v0.1.0`, justfile git fallback
  `@v0.1.0`, compose `SOURCES_IMAGE` default `:0.1.0`. The local sibling
  clone was deleted — the sibling-editable path in `just install-dev`
  remains for cross-repo development but is no longer the default reality.
- Still open: parent branch publication (gated on Task 09), declaring the
  dependency for parent CI, the compatibility matrix, and digest pinning
  for production.

## Progress (2026-07-29) — parent dependency contract

Implementation steps 4–9 are done in the parent. Distribution mechanism
decision (step 1): **tagged VCS dependency** — the child is not on PyPI and a
registry adds release friction for no benefit while both repos move together.

- **Declared** (`pyproject.toml`): `open-pulse-sources @ git+…@v0.1.1` as a
  hard dependency, per the "mandatory, not an extra" decision above. That
  entry is now the **single source of truth** for the version.
- **De-duplicated** the pin from the four places that carried their own copy:
  `justfile` (git fallback branch), `.github/workflows/ci.yml` (both jobs),
  and `tools/image/Dockerfile`. All four now inherit it from the project
  install. The Dockerfile's `OPEN_PULSE_SOURCES_REF` build arg still exists
  but defaults to **empty** — an override-only escape hatch for testing an
  unreleased child revision (step 8: the publishing workflow passes no arg, so
  the pin travels with the source revision instead of inheriting a branch).
- **Sibling checkout** (step 6): `just install-dev` now detects
  `./open-pulse-sources` *or* `../open-pulse-sources` and re-installs it
  editable, echoing which path it took.
- **Drift guard** (new, `tests/v2/test_open_pulse_sources_pin.py`, 4 tests):
  fails if the library pin is mutable or malformed, if the `gme-sources` image
  tag drifts from the library pin, or if any install path re-pins the library
  with a literal ref. Reuses `_resolve_compose_image` for the `${VAR:-default}`
  form.
- **Provenance**: the image publishing workflow extracts the pin from
  `pyproject.toml` (failing the build if absent) and stamps it as the
  `ch.sdsc.pulse.open-pulse-sources-ref` OCI label, so a deployment smoke test
  can compare `gme-api`'s library revision against the running `gme-sources`
  image without a rebuild.
- **Compatibility matrix** (step 9): README "Cross-repo compatibility" section
  + a CHANGELOG entry; `AGENTS.md` gained a "Cross-repo version pin" section
  so future agents don't reintroduce a second pin.

Evidence: `uv pip install --dry-run` of the parent in a clean 3.12 venv
resolves `open-pulse-sources==0.1.1 (from git+…@b98b85dd…)` — i.e. a clean
install now brings the library, pinned to the immutable commit behind the tag
(acceptance criteria 1 and 2). 4/4 pin tests pass; the drift detection was
verified in both directions.

### Blocker discovered: no published child tag has the 503 fix

Child `main` (`841e0fa`) is **4 commits ahead of `v0.1.1`** (`b98b85d`), and
those commits include `fix(service): missing-credential config errors surface
as 503, not raw 500` — the finding this backlog recorded from the compose
validation run and tracked under Task 04. Consequences:

- The `v0.1.1` library pin and the `:0.1.1` image both predate that fix.
- Pinning to child `main` instead would violate "no production default uses a
  mutable ref" (and the new drift guard rejects it).

**Resolved 2026-07-29 — child `v0.1.2` released.** Child commit `9beef91`
(`chore(release): 0.1.2`) bumps the version + `uv.lock` and adds the changelog
entry; tag `v0.1.2` pushed, child CI publishes `…/open-pulse-sources:0.1.2`.
The parent is re-pinned to it in the same change: `pyproject.toml` →
`@v0.1.2`, compose `gme-sources` → `:0.1.2`, README matrix row, and a
CHANGELOG note that `v0.1.1` and earlier are unsupported by `3.0.0`.

## Implementation steps

1. Complete Task 01 and all required child quality gates.
2. Publish the child feature branch for review.
3. Create an immutable child tag/release after merge.
4. Make the parent dependency explicit:
   - declare the exact supported tag/version, or
   - make imports genuinely optional and isolate all RAG-dependent paths.
5. Align `just install`, `just install-dev`, `just setup`, devcontainer, CI,
   and Docker around the same dependency rule.
6. Fix local-checkout detection. The current recipe checks a nested
   `open-pulse-sources/`; document that layout or support a conventional
   sibling checkout explicitly.
7. Pin parent Docker's Python library and `gme-sources` image to the same child
   release/SHA. Prefer immutable image digests for production.
8. Pass the pin explicitly from the image publishing workflow instead of
   inheriting `main`.
9. Document the compatibility matrix in both changelogs/readmes.
10. Publish the parent split branch only after cross-repo checks pass.

## Acceptance criteria

- A clean parent install can import `src.api`.
- Parent CI and Docker install the same immutable child version.
- The deployed child service image is built from that same source revision.
- No production default uses `main` or `latest`.
- A compatibility statement identifies supported parent/child version pairs.
- Private-repository authentication, if still required, is supplied securely
  via build secrets and is not embedded in image layers.

## Verification

```bash
uv run --isolated --extra dev python -c "import src.api"
python -m pytest tests/v2/test_doi_canonicalization_v2.py -q
docker build ... --build-arg OPEN_PULSE_SOURCES_REF=<immutable-ref>
```

Also compare the installed library version/SHA with the running sources image
metadata during deployment smoke tests.

