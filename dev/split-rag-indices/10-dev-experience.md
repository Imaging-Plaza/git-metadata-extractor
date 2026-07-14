# Task 10 — Developer-experience modernization

**Severity:** P2 · **Status:** Ready — parallel; best after Task 09 shrinks
the surface · **Repositories:** both

## Objective

Make both repos straightforward to develop against: small modules, one-command
bootstrap, CI parity, and honest naming — "look at the future" rather than
carrying monolith ergonomics forward.

## Findings (verified 2026-07-14)

- Parent `src/v2/api.py` is still ~2,500 lines after the split (extract
  routes + jobs + crawl + cache + auto-ingest closures in one module).
- Child inherited `V2_*` env-var names (`V2_PROVIDER_CACHE_PATH`, …) that
  are meaningless in a repo with no "v2"; the split backlog already listed the
  rename as pre-1.0 backlog.
- Parent has CI, child has none (Task 03); once Task 03 lands, the parent
  should reuse the same job shapes (quality/tests/package/image) so both
  repos feel identical to contributors.
- Fresh-clone bootstrap requires knowing about the sibling-clone install
  rule; `just setup` still scaffolds a v1-era `.env`.

## Implementation steps

1. **Split `src/v2/api.py` into routers**: `extract.py` (extract + jobs +
   crawl), `cache.py`, `auto_ingest.py` (the four `_maybe_schedule_*`
   closures + their helpers), keeping route paths identical. Pure
   mechanical move with the existing tests as the safety net.
2. **Child env-var rename** (pre-1.0, coordinate with Task 03 CI):
   `V2_PROVIDER_CACHE_*` → `SOURCES_CACHE_*` (or similar), with one
   release of dual-read + deprecation warning; update compose files and
   docs in the same change.
3. **One-command bootstrap** in each repo: `just setup` creates the venv,
   installs deps (incl. the cross-repo dependency rule from Task 02),
   scaffolds a current `.env` template (no v1 vars, includes API_TOKEN
   generation hint), and runs the fast test target as a smoke check.
4. **CI parity**: port the Task 03 job shapes to the parent (quality,
   tests, package/import-contract, image build) so a contributor sees the
   same gates in both repos.
5. **Naming sweep**: the child service module is `service/`, its app title
   and README should present it as the "Open Pulse Sources API" — drop
   remaining "v2"/GME phrasing except where route-compatibility requires
   the `/v2` prefix (that stays until a versioned rename is planned).
6. Refresh AGENTS.md in both repos to the post-split, post-v1 reality so
   agent contributors get an accurate map (parent AGENTS.md still
   documents v1 today; child has none — write one).
7. Make `V2_USE_MOCK_PROVIDERS=true` usable in a container, or document it
   as checkout-only: mock providers load fixtures from `tests/v2/fixtures/`,
   which the image does not ship (2026-07-14 smoke run: mock-mode extract
   500s in the image until `tests/` is bind-mounted). Ship the fixtures
   with the mock providers or fail with a clear message.

## Acceptance criteria

- No single API module over ~800 lines in the parent; routes unchanged
  (contract tests from Task 04 stay green).
- Child env vars renamed with a documented deprecation window.
- `just setup && just test` works from a fresh clone of either repo.
- Both repos run the same CI job shapes.
- AGENTS.md exists and is accurate in both repos.

## Non-goals

- No route renames/removals beyond Task 09 (the `/v2` prefix compatibility
  contract stays).
- No framework changes; FastAPI + uv + just remain the stack.
