# Task 02 — Publish and pin the cross-repository release

**Severity:** P0 · **Status:** Blocked by Task 01 · **Repositories:** both

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

