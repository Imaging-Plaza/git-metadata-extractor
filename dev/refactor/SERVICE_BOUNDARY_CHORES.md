# Service boundary follow-up plan

**Goal:** Finish the four agreed maintenance tasks without publishing either
repository or changing local secrets/data.

**Design:** One JSON compatibility lock records the independent sources commit
and reported version. A standard-library bootstrap checks existing checkouts
without changing them, or fetches that exact commit into a new checkout. CI
runs the optional ASGI contracts and the same isolated Docker smoke used locally.
Publication drops the obsolete Python-library pin and records the tested remote
service revision instead. A source revision must be published before remote CI
can fetch it; no fallback to main or the old image is allowed.

**Execution:** Inline implementation using the executing-plans workflow on the
existing local refactor branch. No extra worktree is needed for this continuation.

- [x] Add compatibility lock, safe checkout command and temporary-Git tests.
- [x] Correct the publishing workflow and extractor environment template.
- [x] Promote the Docker smoke into tracked tooling and wire contracts/smoke
  into CI, with hard failures when prerequisites are missing.
- [x] Document fresh setup and release ordering, validate locally, commit.

**Validation:** Bootstrap tests exercise pinned checkout, offline idempotence,
wrong revision/origin, dirty checkout, non-repository destination and malformed
lock rejection. Run workflow lint, HTTP contracts, actual container smoke,
strict docs build, and focused architectural checks. No live model/provider calls.

## Validation and rulings (2026-11-12)

- `python -m unittest discover -s tests/dev -v`: 10 passed, using temporary
  Git repositories. Initial missing-module failure demonstrated the bootstrap
  test gate; retry regression failed before the empty-checkout fix and passed
  afterward. Existing branches, local work and ignored data remain protected.
- `python scripts/dev/sources_checkout.py --check`: verified the existing
  sources commit without network or mutations.
- `pytest tests/contracts/test_sources_http_contract.py tests/v2/test_sources_boundary.py`:
  7 passed in the disposable validation container, network disabled.
- `python scripts/dev/smoke_sources.py`: passed with both existing production
  images, synthetic credentials, temporary DuckDB fixture and internal network.
- Ruff on all new Python files, actionlint on all three affected workflows,
  whitespace checks, and the strict Pages build passed.
- The combined contract-test dependency declarations resolved successfully in
  a disposable container. The first dry-run used read-only project mounts and
  could not write editable metadata; repeating with temporary metadata copies
  resolved successfully without modifying either checkout.
- Native `just` is unavailable on this workstation; the Python commands behind
  the recipes were executed directly. No claim of a hosted Actions run is made.
- Independent review found one retry issue, fixed with regressions; final review
  reported no remaining findings.

**Ruling:** Keep the sources revision local and unpublished, consistent with the
requested local workflow. The compatibility lock records actual tested code.
Fresh remote setup and hosted CI/publication will fail until that commit is
published; they never fall back to an untested branch. Release ordering and
immutable deployment image selection are documented, without inventing a tag
or registry digest. No remote pushes, image publication, secret-file changes,
existing service restarts or live data/model calls were performed.
